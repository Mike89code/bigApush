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
                       fwd_return, DB)
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
def run_filter(items, use_flow=True):
    G = {"low": [], "watch": [], "exclude": []}
    for d in items:
        ex, wt = [], []

        # 层② 流动性（硬门槛）
        amt = d.get("avg_amt20_wan")
        if amt is not None and amt < 5000:
            ex.append("流动性不足（20日日均 %.0f 万，低于 5000 万门槛）" % amt)

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

        d["exclude_reasons"] = ex
        d["watch_reasons"] = wt
        if ex:
            G["exclude"].append(d)
        elif wt:
            G["watch"].append(d)
        else:
            G["low"].append(d)
    return G

# ---------- 风险分级（A/B/C）用于"相对择优"组 ----------
def grade(d):
    """A=无附加风险 | B=有单项轻微瑕疵 | C=仍有需跟踪的实质事项"""
    sn = d.get("sector_net_yi")
    amt = d.get("avg_amt20_wan") or 0
    rsi = d.get("rsi") or 0
    rd = d.get("res_dist_pct")
    ann = d.get("ann") or {}
    pts = 0
    if ann.get("S3"):
        pts += 1
    if amt < 8000:
        pts += 1
    if rsi > 70:
        pts += 1
    if rd is not None and rd < 4:
        pts += 1
    if sn is not None and sn < 0:
        pts += 1
    return "A" if pts == 0 else ("B" if pts <= 2 else "C")

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

    G = run_filter(items, use_flow=(fwd is None))   # 回测时剔除层①
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
