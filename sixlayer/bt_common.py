# -*- coding: utf-8 -*-
"""BT: 通用回测模块。

关键点：所有指标必须只用 cutoff 当天及之前的数据计算，否则是前视偏差。
- indicators(code, cutoff)  -> 只取 date <= cutoff 的 K 线
- classify_anns(anns, cutoff) -> 只取 notice_date <= cutoff 的公告

DB 路径多候选：既兼容仓库内 sixlayer/ 布局（../data），也兼容本地
报告/tools/ 布局（../../bigApush/data）。优先取存在的那个。
"""
import os, json, ssl, sqlite3, time, urllib.request, re

HERE = os.path.dirname(os.path.abspath(__file__))

_DB_CANDIDATES = [
    os.path.join(HERE, "..", "data", "stock_selection.db"),             # 仓库内：sixlayer/../data
    os.path.join(HERE, "..", "..", "bigApush", "data", "stock_selection.db"),  # 本地：报告/tools/../../bigApush
    os.path.join(HERE, "data", "stock_selection.db"),
]
DB = None
for _p in _DB_CANDIDATES:
    if os.path.exists(_p):
        DB = os.path.abspath(_p)
        break
if DB is None:
    DB = os.path.abspath(_DB_CANDIDATES[0])

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
     "Referer": "https://data.eastmoney.com/notices/", "Accept": "*/*",
     "Accept-Language": "zh-CN,zh;q=0.9"}

# ================= 公告严重度分级（本次评审核心改动） =================
# 分级原则：按「实际杀伤力 + 确定性」排，不再一刀切
S1_EXCLUDE = [   # 一级：直接排除（确定性重大利空）
    "业绩预亏", "业绩预减", "预计亏损", "首亏", "续亏", "净利润下降",
    "业绩大幅下降", "立案调查", "立案告知", "行政处罚", "公开谴责",
    "被实施退市风险警示", "终止上市", "涉嫌犯罪", "涉嫌违法", "严重违法失信",
]
S2_DOWNGRADE = [ # 二级：降级到观望（尚未落地 / 治理层面瑕疵 / 可能不执行）
    "减持计划", "拟减持", "计划减持", "减持预披露", "减持股份的预披露",
    "计提减值", "大额计提", "司法拍卖", "行政监管措施", "股份冻结",
]
S3_INFO = [      # 三级：仅提示，不剔除（多属利空出尽或例行事项）
    "减持结果", "减持完成", "实施完毕", "期限届满", "提前终止", "终止减持",
    "减持进展", "权益变动触及", "商誉减值测试", "减值测试报告",
    "解禁", "限售股上市", "诉讼", "仲裁", "问询函", "关注函", "补充公告",
    "股权质押", "补充质押",
]
# 方向纠正：命中关键词但方向相反（利好 / 利空出尽）
NEUTRALIZE = [
    "撤销退市风险", "撤销其他风险警示", "撤销*ST", "撤销ST", "摘帽", "恢复上市",
]

# ⚠️ 「减持」这类词会被插词破坏匹配（真实标题是「减持【股份】结果」而非「减持结果」），
#    因此单独用「减持 + 后缀」的组合判断，不走通用关键词表。
ZH_JIAN_S2 = ["计划", "预披露", "拟减持", "计划减持"]
ZH_JIAN_S3 = ["结果", "完成", "完毕", "届满", "终止", "进展", "过半", "期满"]

def classify_anns(anns, cutoff):
    """只使用 cutoff 及之前的公告；返回 {S1:[], S2:[], S3:[]}"""
    out = {"S1": [], "S2": [], "S3": []}
    for a in anns:
        d = a.get("date") or ""
        if d > cutoff:          # 前视偏差防线
            continue
        text = a["title"] + " " + a.get("col", "")
        # 方向纠正（利好）
        if any(nk in text for nk in NEUTRALIZE):
            out["S3"].append(a)
            continue
        # 减持类单独判定（防插词漏匹配）
        if "减持" in text:
            if any(k in text for k in ZH_JIAN_S2):
                out["S2"].append(a)
            else:
                out["S3"].append(a)
            continue
        hit = False
        for key, lst in (("S1", S1_EXCLUDE), ("S2", S2_DOWNGRADE), ("S3", S3_INFO)):
            for k in lst:
                if k in text:
                    out[key].append(a)
                    hit = True
                    break
            if hit:
                break
    return out

# ================= 指标（严格 cutoff） =================
def load_klines(code, cutoff, n=200):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "select date, open, high, low, close, volume from stock_kline "
        "where code=? and date<=? order by date desc limit ?",
        (code, cutoff, n)).fetchall()
    c.close()
    rows.reverse()
    return rows

def calc_rsi(closes, period=14):
    if len(closes) < period + 2:
        return None
    gains = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)

def indicators(code, cutoff):
    rows = load_klines(code, cutoff)
    if not rows:
        return None
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    cur = closes[-1]
    n20 = min(20, len(rows))
    amts = [closes[len(closes)-1-i] * vols[len(vols)-1-i] * 100 for i in range(n20)]
    avg_amt = sum(amts) / n20 if amts else 0
    rsi = calc_rsi(closes)
    # 量比：当日量 / 20日均量（>1.5 放量，<0.8 缩量）
    v20 = sum(vols[-20:]) / len(vols[-20:]) if vols else None
    vol_ratio = round(vols[-1] / v20, 2) if (v20 and v20 > 0) else None
    # 20 日涨幅（相对强度基准的分子）
    pct20 = round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None
    # 近 20 日跌停检测（主板 10% 板：pct ≤ -9.8%；创业/科创 20% 板：≤ -19.8%）
    board = 20 if code.startswith(("30", "68")) else 10
    lim = -9.8 if board == 10 else -19.8
    hit_ld = False
    for r in rows[-20:]:
        if r[3] and r[1]:  # high, open 存在才算 pct
            prev_close = None
            i = rows.index(r)
            if i > 0:
                prev_close = rows[i-1][4]
            if prev_close:
                pct = (r[4] / prev_close - 1) * 100
                if pct <= lim:
                    hit_ld = True
                    break
    # 压力位：近 120 日高于现价的最近前高 + ±2% 触碰次数
    win = rows[-120:]
    above = [(r[1], r[2]) for r in win if r[2] > cur * 1.005]
    if above:
        res_price = above[-1][1]
        res_dist = (res_price - cur) / cur * 100
        touch = sum(1 for r in win if abs(r[2] - res_price) / res_price <= 0.02)
        # 测量幅度法第二目标（突破位 + 形态高度；形态高度≈前高-近20日最低）
        low20 = min(r[3] for r in rows[-20:])
        meas_target = round(res_price + (res_price - low20), 3)
    else:
        res_price = res_dist = touch = None
        meas_target = None
    return {"last_date": rows[-1][0], "close": cur,
            "avg_amt20_wan": round(avg_amt / 1e4, 1),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "ma5": round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else None,
            "ma10": round(sum(closes[-10:]) / 10, 3) if len(closes) >= 10 else None,
            "vol_ratio": vol_ratio, "pct20": pct20,
            "kline_n": len(rows), "hit_limit_down": hit_ld,
            "res_price": round(res_price, 3) if res_price else None,
            "res_dist_pct": round(res_dist, 2) if res_dist is not None else None,
            "res_touch": touch,
            "meas_target": meas_target}


# ================= 评级（v5.4：纯由评分推导，消灭"68分A级/75分C级"倒挂） =================
def grade_from_score(score):
    """≥90 A ｜ 75~89 B ｜ 60~74 C ｜ <60 D。"""
    if score is None:
        return "C"
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


# ================= 量比 × 形态解读（v5.3 核心：量比好坏取决于形态） =================
# strategy_key: (lo, hi, kind)   kind: breakout=缩量是硬伤 / pullback=缩量是健康洗盘 / neutral
VOL_RULES = {
    "ResistanceBreakoutStrategy": (1.5, 3.0, "breakout"),   # 阻力突破：1.5~3.0 倍
    "MultiGoldenCrossStrategy": (1.0, 2.0, "breakout"),     # 多金叉：温和放量最佳
    "GoldenTriangleStrategy": (0.5, 0.9, "pullback"),       # 金三角：缩量回踩=洗盘结束
    "Strategy2560Selection": (0.8, 1.2, "neutral"),         # 2560：中性
    "LimitUpPullbackStrategy": (0.3, 0.8, "pullback"),      # 涨停回踩：回踩缩量健康
    "LimitUpSidewaysStrategy": (0.8, 1.5, "neutral"),       # 涨停横盘
    "WBottomStrategy": (1.2, 2.5, "breakout"),              # W底：破颈线要放量
    "TrendStartStrategy": (0.8, 2.0, "neutral"),
    "StrongWashWeakToStrongStrategy": (1.0, 2.5, "breakout"),
    "MultiPartyCannonStrategy": (1.0, 2.5, "breakout"),     # 多炮
    "ImmortalGuidanceStrategy": (0.5, 1.2, "pullback"),     # 仙人指路：缩量回档健康
    "TrendResonanceReversalStrategy": (0.8, 2.0, "neutral"),
    "TrendAccelerationInflectionStrategy": (0.8, 2.0, "neutral"),
    "GoldenCrossNotGreenStrategy": (0.8, 2.0, "neutral"),
}
DEFAULT_VOL_RULE = (0.8, 2.0, "neutral")


def vol_check(d):
    """量比分形态解读。返回 (verdict, txt)；verdict: good/warn/bad/None。"""
    vr = d.get("vol_ratio")
    if vr is None:
        return None, ""
    rule = None
    for s in (d.get("strats") or []):
        if s in VOL_RULES:
            rule = VOL_RULES[s]
            break
    if rule is None:
        rule = DEFAULT_VOL_RULE
    lo, hi, kind = rule
    if lo <= vr <= hi:
        return "good", "量比 %.2f ✅符合形态" % vr
    if vr < lo:
        if kind == "pullback":
            return "good", "量比 %.2f ✅缩量整理健康" % vr
        if kind == "breakout":
            return "bad", "量比 %.2f ❌突破未放量，待确认" % vr
        return "warn", "量比 %.2f ⚠️偏缩量" % vr
    # vr > hi
    if vr > hi * 1.5:
        return "warn", "量比 %.2f ⚠️异常巨量，防对倒" % vr
    if kind == "pullback":
        return "warn", "量比 %.2f ⚠️回踩放量，防出货" % vr
    return "good", "量比 %.2f 🔥放量" % vr


# ================= 六层过滤评分（v5.3 核心：因子真实参与决策） =================
def s6_score(d):
    """六层过滤自己的 0-100 评分（advice_log 的 score 从未有值，此前排序/展示都是空的）。

    构成（基准 60）：
      形态   双策略共振 +8 / 单策略 +4
      RSI    40~65 +6；66~70 +3
      RS超额 >+2pp +8；0~+2pp +4；-2~0pp 0；<-2pp -8
      量比   分形态：符合 +6；异常(bad) -6；warn 0
      行业   净流入 +4 / 净流出 -4 / 缺失 0
      压力位 距离<2% -4；2~5% +2；无上方压力 +4
      流动性 日均≥2亿 +4；≥1亿 +2
    """
    s = 60.0
    s += 8 if len(d.get("strats") or []) >= 2 else 4
    rsi = d.get("rsi")
    if rsi is not None:
        if 40 <= rsi <= 65:
            s += 6
        elif 65 < rsi <= 70:
            s += 3
    ex = d.get("rs_excess")
    if ex is not None:
        if ex > 2:
            s += 8 + min(6, (ex - 2) * 3)   # 连续加分：超额越多分越高，封顶 +14
        elif ex >= 0:
            s += 4
        elif ex < -2:
            s -= 8
    v, _ = vol_check(d)
    if v == "good":
        s += 6
    elif v == "bad":
        s -= 6
    sn = d.get("sector_net_yi")
    if sn is not None:
        s += 4 if sn > 0 else -4
    rd = d.get("res_dist_pct")
    if rd is None:
        s += 4
    elif rd < 2:
        s -= 4
    elif rd <= 5:
        s += 2
    amt = d.get("avg_amt20_wan") or 0
    if amt >= 20000:
        s += 4
    elif amt >= 10000:
        s += 2
    return round(max(0, min(100, s)))


# ================= 相对强度基准（沪深300 20日涨幅） =================
IDX_CACHE = os.path.join(HERE, "idx_pct20.json")

def idx_pct20(date):
    """沪深300 近 20 日涨幅（%，截至 date 当日）。腾讯 fqkline，双域名兜底。"""
    cache = {}
    if os.path.exists(IDX_CACHE):
        try:
            cache = json.load(open(IDX_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    if cache.get(date) is not None:
        return cache[date]
    hosts = ["https://web.ifzq.gtimg.cn", "https://proxy.finance.qq.com"]
    pct = None
    for h in hosts:
        try:
            u = (h + "/appstock/app/fqkline/get?param=sh000300,day,,,25,qfq")
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            days = (d.get("data", {}).get("sh000300", {})
                    .get("qfqday") or d.get("data", {}).get("sh000300", {}).get("day"))
            if days and len(days) >= 21:
                c_new = float(days[-1][2])
                c_old = float(days[-21][2])
                if c_old:
                    pct = round((c_new / c_old - 1) * 100, 2)
            break
        except Exception:
            continue
    if pct is not None:
        cache[date] = pct
        with open(IDX_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    return pct


# ================= 总市值（qt.gtimg.cn 批量实时行情） =================
def fetch_market_caps(codes):
    """批量取总市值（亿）。返回 {code: 亿 or None}；网络失败返回 {}。"""
    out = {}
    qs = []
    for c in codes:
        pre = "sh" if c.startswith(("6", "9")) else ("bj" if c[0] in "48" else "sz")
        qs.append(pre + c)
    for i in range(0, len(qs), 30):
        batch = qs[i:i + 30]
        try:
            u = "https://qt.gtimg.cn/q=" + ",".join(batch)
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                t = r.read().decode("gbk", "replace")
            for line in t.strip().split(";"):
                line = line.strip()
                if "~" not in line:
                    continue
                seg = line.split("~")
                code = seg[2] if len(seg) > 2 else ""
                mv = None
                if len(seg) > 45:
                    try:
                        v = float(seg[45])
                        if 5 < v < 100000:   # 合理性检查（亿）
                            mv = v
                    except ValueError:
                        pass
                if code:
                    out[code] = mv
        except Exception:
            continue
    return out

# ================= 名单 =================
def build_pool(date):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    pool = {}
    for r in c.execute("select strategy_key, stock_code, stock_name from strategy_pick_log "
                       "where selection_date=?", (date,)):
        k = r["stock_code"]
        d = pool.setdefault(k, {"code": k, "name": re.sub(r"\s+", "", r["stock_name"] or ""),
                                "strats": []})
        if r["strategy_key"] not in d["strats"]:
            d["strats"].append(r["strategy_key"])
    for r in c.execute("select code, name, score from advice_log where trade_date=?", (date,)):
        d = pool.setdefault(r["code"], {"code": r["code"],
                                        "name": re.sub(r"\s+", "", r["name"] or ""),
                                        "strats": []})
        d["score"] = r["score"]
    for k, d in pool.items():
        if not d["name"]:
            row = c.execute("select name from stock_basic where code=?", (k,)).fetchone()
            if row:
                d["name"] = re.sub(r"\s+", "", row["name"])
    c.close()
    return sorted(pool.values(), key=lambda x: (-(x.get("score") or 0), x["code"]))

# ================= 公告（原文缓存，含日期） =================
ANN_CACHE = os.path.join(HERE, "bt_ann_cache.json")

def _load_cache():
    if os.path.exists(ANN_CACHE):
        with open(ANN_CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def fetch_ann_raw(code, cache):
    if code in cache:
        return cache[code]
    out = []
    u = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
         "page_size=60&page_index=1&ann_type=A&client_source=web&stock_list=%s"
         "&f_node=0&s_node=0" % code)
    for a in range(3):
        try:
            req = urllib.request.Request(u, headers=H)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                b = r.read()
            d = json.loads(b.decode("utf-8", "replace")) if b else None
            if d and d.get("data") and d["data"].get("list"):
                for it in d["data"]["list"]:
                    ac = it.get("art_code") or ""
                    out.append({
                        "date": (it.get("notice_date") or "")[:10],
                        "title": (it.get("title") or "").strip(),
                        "col": " ".join((cc.get("column_name") or "")
                                        for cc in (it.get("columns") or [])).strip(),
                        "art": ac,
                        # 东财公告详情页，用于回溯核验
                        "url": ("https://data.eastmoney.com/notices/detail/%s/%s.html"
                                % (code, ac)) if ac else "",
                    })
            break
        except Exception:
            if a == 2:
                break
            time.sleep(1.0 + a)
    cache[code] = out
    return out

def ensure_anns(codes):
    cache = _load_cache()
    todo = [c for c in codes if c not in cache]
    for i, c in enumerate(todo):
        fetch_ann_raw(c, cache)
        time.sleep(0.4)
        if (i + 1) % 20 == 0:
            print("   公告 %d/%d" % (i + 1, len(todo)))
            with open(ANN_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    with open(ANN_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return cache

# ================= 前瞻收益 =================
def fwd_return(code, d0, d1):
    c = sqlite3.connect(DB)
    r0 = c.execute("select close from stock_kline where code=? and date=?", (code, d0)).fetchone()
    r1 = c.execute("select close from stock_kline where code=? and date=?", (code, d1)).fetchone()
    c.close()
    if not r0 or not r1 or not r0[0]:
        return None
    return (r1[0] - r0[0]) / r0[0] * 100
