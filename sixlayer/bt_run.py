# -*- coding: utf-8 -*-
"""BT_RUN: 跑六层过滤（同一套规则）。

CI 用法：
    python bt_run.py                 # 当日过滤（不回测）
    python bt_run.py 2026-09-18 2026-09-22   # 回测（前瞻到 09-22）

回测诚实性说明：
- 层①②③⑤ 中，②③⑤ 可由 K 线完整历史重算，无前视偏差；
  ① 依赖"行业资金流"，该数据只有当日快照、无历史存档 → **回测中剔除层①**，
  并在报告中明确标注「层①未参与回测」。
- 层④ 用公告接口，按 notice_date <= cutoff 过滤，避免前视偏差。
- 行业分类取当前值（行业极少变动），属已知的轻微近似，报告中标注。
"""
import os, json, sys, time, ssl, urllib.request, statistics, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bt_common import (build_pool, indicators, classify_anns, ensure_anns,
                       fwd_return, DB, idx_pct20, fetch_market_caps,
                       s6_score, grade_from_score)
from s6_bridge import EM2SINA

# ---------- F10 行业（复用 + 增量） ----------
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HF = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://emweb.securities.eastmoney.com/"}

def f10_industry(code):
    mk = "SH" if code.startswith(("6", "9")) else ("BJ" if code[0] in "48" else "SZ")
    u = ("https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/"
         "CompanySurveyAjax?code=%s%s" % (mk, code))
    for a in range(3):
        try:
            req = urllib.request.Request(u, headers=HF)
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                b = r.read()
            d = json.loads(b.decode("utf-8", "replace"))
            jb = d.get("jbzl") or {}
            return {"industry": jb.get("sshy"), "industry2": jb.get("sszjhhy"),
                    "area": jb.get("qy")}
        except Exception:
            if a == 2:
                return None
            time.sleep(1.2 + a)
    return None

F10_CACHE = os.path.join(HERE, "s3e_f10.json")
fcache = {}
if os.path.exists(F10_CACHE):
    fcache.update(json.load(open(F10_CACHE, encoding="utf-8")))

def ensure_industry(codes):
    todo = [c for c in codes if not (fcache.get(c) or {}).get("industry")]
    for i, c in enumerate(todo):
        fcache[c] = f10_industry(c)
        time.sleep(0.35)
    if todo:
        with open(F10_CACHE, "w", encoding="utf-8") as f:
            json.dump(fcache, f, ensure_ascii=False, indent=1)
    print("   行业: 新增 %d，总计可用 %d" % (
        len(todo), sum(1 for v in fcache.values() if v and v.get("industry"))))

# ---------- 行业资金流（只有当日快照） ----------
def load_flow():
    p = os.path.join(HERE, "s6_flow.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {}

FLOW = load_flow()

# ---------- 过滤引擎（新规则，含分级） ----------
def run_filter(items, use_flow=True, idx20=None):
    G = {"low": [], "watch": [], "exclude": []}
    for d in items:
        ex, wt = [], []

        # 层② 流动性（硬门槛）
        amt = d.get("avg_amt20_wan")
        if amt is not None and amt < 5000:
            ex.append("流动性不足（20日日均 %.0f 万，低于 5000 万门槛）" % amt)

        # 层⓪ 前置过滤（v5.2 新增：次新 / 近期跌停 / 市值 / 大幅跑输大盘）
        kn = d.get("kline_n")
        if kn is not None and kn < 120:
            ex.append("次新股（K线仅 %d 根，上市不满 120 个交易日）" % kn)
        if d.get("hit_limit_down"):
            ex.append("近 20 日内出现过跌停（出货/风险信号）")
        mv = d.get("mktcap_yi")
        if mv is not None and mv < 30:
            ex.append("总市值 %.1f 亿，低于 30 亿门槛（易被操纵）" % mv)
        ex20 = d.get("rs_excess")
        if ex20 is not None and idx20 is not None and ex20 < -5:
            ex.append("20日跑输沪深300 %.1f 个百分点（相对强度过弱）" % abs(ex20))

        # 层④ 公告分级
        ann = d.get("ann") or {"S1": [], "S2": [], "S3": []}
        if ann.get("S1"):
            ex.append("一级利空（%s）：%s" % (ann["S1"][0].get("kw", "重大"),
                                          ann["S1"][0]["title"][:30]))
        if ann.get("S2"):
            wt.append("二级关注：%s" % ann["S2"][0]["title"][:32])

        # 层③ 超买
        rsi = d.get("rsi")
        if rsi is not None and rsi > 75:
            wt.append("RSI %.1f 超买，禁止追涨，只观察回踩" % rsi)

        # 层⑤ 压力位
        rd = d.get("res_dist_pct")
        if rd is not None and rd < 3:
            wt.append("距上方 %s 元压力位 %.2f%%" % (d.get("res_price"), rd))

        # 层① 板块资金（仅当日快照）
        if use_flow:
            sn = d.get("sector_net_yi")
            if sn is not None and sn < 0:
                wt.append("所属【%s】当日资金净流出 %.2f 亿"
                          % (d.get("industry") or "?", abs(sn)))

        # 量能标注（先攒数据，不做硬门槛）：缩量突破可靠性打折
        vr = d.get("vol_ratio")
        if vr is not None and vr < 0.8:
            wt.append("量比 %.2f 缩量（突破可靠性打折）" % vr)

        d["exclude_reasons"] = ex
        d["watch_reasons"] = wt
        if ex:
            G["exclude"].append(d)
        elif wt:
            G["watch"].append(d)
        else:
            G["low"].append(d)

    # 行业分散：low 组同行业只留评分最高 1 只，其余降观望（防板块一起挨打）
    best = {}
    for d in G["low"]:
        ind = d.get("industry") or "?"
        if ind not in best or (d.get("score") or 0) > (best[ind].get("score") or 0):
            best[ind] = d
    demoted = [d for d in G["low"]
               if best.get(d.get("industry") or "?") is not d]
    if demoted:
        for d in demoted:
            top = best.get(d.get("industry") or "?")
            d.setdefault("watch_reasons", []).append(
                "同行业（%s）已有更高分 %s %s，分散风险降级"
                % (d.get("industry") or "?", top["code"], top["name"]))
        G["low"] = [d for d in G["low"] if d not in demoted]
        G["watch"].extend(demoted)
    return G

# ---------- 风险分级（v5.4：评级纯由 s6 评分推导，消灭倒挂） ----------
def grade(d):
    return grade_from_score(d.get("score"))

# ================= 主流程 =================
def run(date, fwd=None, label=""):
    print("=" * 70)
    print("【%s】%s  前瞻到 %s" % (label, date, fwd or "-"))
    print("=" * 70)
    items = build_pool(date)
    print("候选池: %d 只" % len(items))
    for d in items:
        ind = indicators(d["code"], date)
        if ind:
            d.update(ind)
        d["ann_raw"] = []
    ensure_industry([d["code"] for d in items])
    cache = ensure_anns([d["code"] for d in items])
    for d in items:
        raw = cache.get(d["code"], [])
        cls = classify_anns(raw, date)
        # 记关键词便于展示
        for key in ("S1", "S2", "S3"):
            for a in cls[key]:
                for k in (["业绩预亏", "立案调查", "行政处罚"] if key == "S1" else []):
                    if k in a["title"]:
                        a["kw"] = k
                        break
        d["ann"] = cls
        d["industry"] = (fcache.get(d["code"]) or {}).get("industry")
        sn = FLOW.get(EM2SINA.get(d["industry"] or "", ""))
        d["sector_net_yi"] = round(sn["net"] / 1e8, 2) if sn else None

    # 前瞻收益
    if fwd:
        for d in items:
            d["fwd_ret"] = fwd_return(d["code"], date, fwd)

    # 相对强度基准（沪深300 20日涨幅）+ 超额收益；总市值批量抓取
    idx20 = idx_pct20(date)
    print("沪深300 20日涨幅: %s" % ("+%s%%" % idx20 if idx20 is not None and idx20 > 0 else ("%s%%" % idx20 if idx20 is not None else "获取失败（跳过 RS 过滤）")))
    caps = fetch_market_caps([d["code"] for d in items]) if items else {}
    for d in items:
        if d.get("pct20") is not None and idx20 is not None:
            d["rs_excess"] = round(d["pct20"] - idx20, 2)
        if d["code"] in caps:
            d["mktcap_yi"] = caps[d["code"]]
        # 六层评分（因子真实参与决策）：覆盖展示分（advice_log 的 score 恒为空）
        d["score"] = s6_score(d)

    G = run_filter(items, use_flow=(fwd is None), idx20=idx20)   # 回测时剔除层①
    for k in G:
        G[k].sort(key=lambda x: (-(x.get("score") or 0), x["code"]))
    for d in G["low"]:
        d["grade"] = grade(d)

    # 统计
    print("\n分组: 相对择优 %d / 观望 %d / 排除 %d"
          % (len(G["low"]), len(G["watch"]), len(G["exclude"])))
    if fwd:
        allr = [d["fwd_ret"] for d in items if d.get("fwd_ret") is not None]
        print("全池基准（%s→%s）: n=%d 均值 %+.2f%% 中位 %+.2f%%"
              % (date, fwd, len(allr), statistics.mean(allr), statistics.median(allr)))
        print()
        print("%-8s %-5s %-9s %-9s %-9s" % ("分组", "n", "均值", "中位", "胜率"))
        for k, cn in (("low", "相对择优"), ("watch", "观望"), ("exclude", "排除")):
            rs = [d["fwd_ret"] for d in G[k] if d.get("fwd_ret") is not None]
            if rs:
                win = sum(1 for x in rs if x > 0) / len(rs) * 100
                print("%-8s %-5d %+9.2f%% %+9.2f%% %8.0f%%"
                      % (cn, len(rs), statistics.mean(rs), statistics.median(rs), win))

    out = {"date": date, "fwd": fwd, "total": len(items),
           "groups": G, "items": items}
    p = os.path.join(HERE, "bt_%s.json" % date.replace("-", ""))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n已写 %s" % os.path.basename(p))
    return out


def today_bj():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 1:
        d0 = args[0]
        fwd = args[1] if len(args) >= 2 else None
        run(d0, fwd=fwd, label=("回测" if fwd else "当期"))
    else:
        run(today_bj(), label="当期")
