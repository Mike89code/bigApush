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
    # 压力位：近 120 日高于现价的最近前高 + ±2% 触碰次数
    win = rows[-120:]
    above = [(r[1], r[2]) for r in win if r[2] > cur * 1.005]
    if above:
        res_price = above[-1][1]
        res_dist = (res_price - cur) / cur * 100
        touch = sum(1 for r in win if abs(r[2] - res_price) / res_price <= 0.02)
    else:
        res_price = res_dist = touch = None
    return {"last_date": rows[-1][0], "close": cur,
            "avg_amt20_wan": round(avg_amt / 1e4, 1),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "res_price": round(res_price, 3) if res_price else None,
            "res_dist_pct": round(res_dist, 2) if res_dist is not None else None,
            "res_touch": touch}

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
