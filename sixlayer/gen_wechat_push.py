# -*- coding: utf-8 -*-
"""生成「v4 六层过滤」风格的微信推送文案（标准推送排版）。

数据源：
  - 当期：bt_<YYYYMMDD>.json（由 bt_run 生成，默认取今日；缺失则取最新一份）
  - 回测基线：bt_backtest_baseline.json（固定历史样本，仅作参考，不参与回测层①）

风格：沿用 bigApush 推送排版（🔍 + ①~⑥ 分段 + 表格 + 边界提示），
内容换成 v4 的"六层机械过滤 + A/B/C 档 + 回测诚实边界"思考方式。

用法：python gen_wechat_push.py  → 写 sixlayer/微信推送-v4风格-<date>.md
"""
import os, json, glob, datetime, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "bt_backtest_baseline.json")

STRAT_CN = {
    "MultiGoldenCrossStrategy": "多金叉", "GoldenTriangleStrategy": "金三角",
    "ResistanceBreakoutStrategy": "阻力突破", "StrongWashWeakToStrongStrategy": "强洗转强",
    "TrendStartStrategy": "趋势起点", "LimitUpPullbackStrategy": "涨停回踩",
    "Strategy2560Selection": "2560战法", "TrendResonanceReversalStrategy": "趋势共振反转",
    "ImmortalGuidanceStrategy": "仙人指路", "LimitUpSidewaysStrategy": "涨停横盘",
    "WBottomStrategy": "W底", "GoldenCrossNotGreenStrategy": "金叉不绿",
    "MultiPartyCannonStrategy": "多炮", "TrendAccelerationInflectionStrategy": "趋势加速拐点",
    "StrongWashWeakToStrong": "强洗转强",
}


def cn_strats(lst):
    return "/".join(STRAT_CN.get(s, s) for s in (lst or [])) or "无"


def sig(v, nd=2):
    return "—" if v is None else ("%." + str(nd) + "f") % v


def grade_flaws(d):
    flaws = []
    ann = d.get("ann") or {}
    if ann.get("S3"):
        flaws.append("三级公告%d条" % len(ann["S3"]))
    if (d.get("avg_amt20_wan") or 0) < 8000:
        flaws.append("日均成交%.1f亿偏小" % ((d.get("avg_amt20_wan") or 0) / 1e4))
    if (d.get("rsi") or 0) > 70:
        flaws.append("RSI%.1f偏高" % d["rsi"])
    rd = d.get("res_dist_pct")
    if rd is not None and rd < 4:
        flaws.append("距压力位%.1f%%近" % rd)
    sn = d.get("sector_net_yi")
    if sn is not None and sn < 0:
        flaws.append("行业净流出%.1f亿" % abs(sn))
    return flaws


def op_line(d):
    s = cn_strats(d.get("strats"))
    sn = d.get("sector_net_yi")
    flow = ("行业当日净流入 %+.1f 亿" % sn) if (sn or 0) > 0 else (
        "行业当日净流出 %.1f 亿" % abs(sn) if sn is not None else "行业资金缺失")
    return "入选策略：%s；所属%s，%s；RSI %s。" % (
        s, d.get("industry") or "未知", flow, sig(d.get("rsi"), 1))


def _today_bj():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


def _load_current():
    # 优先今日；否则取最新一份 bt_*.json（排除基线）
    t = _today_bj().replace("-", "")
    cand = os.path.join(HERE, "bt_%s.json" % t)
    if os.path.exists(cand):
        return json.load(open(cand, encoding="utf-8")), t
    fs = sorted(glob.glob(os.path.join(HERE, "bt_*.json")))
    fs = [f for f in fs if os.path.basename(f) != "bt_backtest_baseline.json"]
    if not fs:
        return None, t
    return json.load(open(fs[-1], encoding="utf-8")), os.path.basename(fs[-1])[3:-5]


def _mean(g, key="fwd_ret"):
    vs = [d[key] for d in g if d.get(key) is not None]
    return (sum(vs) / len(vs), len(vs)) if vs else (0.0, 0)


def main():
    cur = _load_current()[0]
    if cur is None:
        print("!! 找不到当期 bt_*.json，无法生成推送文案")
        return None
    bt = json.load(open(BASELINE, encoding="utf-8"))

    date = cur["date"]
    G = cur["groups"]
    I = cur["items"]
    low, watch, excl = G["low"], G["watch"], G["exclude"]
    n_total = cur.get("total")

    # 回测基线统计（全部动态）
    def bg(k):
        return bt["groups"][k]
    rl, nl = _mean(bg("low"))
    rw, nw = _mean(bg("watch"))
    re_, ne = _mean(bg("exclude"))
    b_items = [x for x in bt["items"] if x.get("fwd_ret") is not None]
    b_all = statistics.mean([x["fwd_ret"] for x in b_items]) if b_items else 0.0
    n_all = len(b_items)
    win_low = (sum(1 for x in bg("low") if (x.get("fwd_ret") or 0) > 0) / nl * 100) if nl else 0.0
    b_date = bt.get("date"); b_fwd = bt.get("fwd")
    n_flow_hit = sum(1 for x in I if (x.get("sector_net_yi") or 0) < 0)

    L = []
    L.append("🔍 **A股六层过滤 · 今日复盘 %s**" % date)
    L.append("")
    L.append("> 候选池 **%d 只** → 相对择优 **%d** ／ 仅观望 **%d** ／ 直接排除 **%d**。"
             % (n_total, len(low), len(watch), len(excl)))
    L.append("> 数据基准：%s 收盘。六层过滤 = ①行业资金 ②流动性 ③超买RSI ④压力位 ⑤公告分级 ⑥综合。"
             % date)
    L.append("")

    # ① 相对择优
    L.append("### ① 相对择优（%d 只，通过全部硬门槛，按 A→B→C 档 + 评分排序）" % len(low))
    L.append("")
    L.append("| # | 标的 | 评分 | 档位 | 策略 | 行业 | RSI | 距压力 | 一句话机会 · 风险 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, d in enumerate(low, 1):
        g = d.get("grade") or "C"
        flaws = grade_flaws(d)
        risk = "；".join(flaws) if flaws else "无附加瑕疵"
        rd = d.get("res_dist_pct")
        gtxt = {"A": "A·无瑕疵", "B": "B·轻微", "C": "C·跟踪"}.get(g, g)
        rdtxt = sig(rd, 1) if rd is not None else "—"
        L.append(f"| {i} | {d['code']} {d['name']} | **{d.get('score')}** | {gtxt} | "
                 f"{cn_strats(d.get('strats'))} | {d.get('industry') or '—'} | "
                 f"{sig(d.get('rsi'), 1)}% | {rdtxt} | {op_line(d)} ｜ {risk} |")
    L.append("")

    # ② 仅观望
    L.append("### ② 仅观望（%d 只，技术成立但触发降级项，不追高）" % len(watch))
    L.append("")
    if watch:
        for d in watch[:8]:
            ws = "；".join(d.get("watch_reasons") or []) or "触发降级项"
            L.append("- **%s %s**（%s）：%s" % (d["code"], d["name"],
                                              cn_strats(d.get("strats")), ws))
    else:
        L.append("- 今日无观望组标的。")
    L.append("")

    # ③ 直接排除
    L.append("### ③ 直接排除（%d 只，触发硬门槛：流动性 / 一级利空）" % len(excl))
    L.append("")
    if excl:
        for d in excl[:6]:
            rs = "；".join(d.get("exclude_reasons") or []) or "触发硬门槛"
            L.append("- **%s %s**：%s" % (d["code"], d["name"], rs))
        if len(excl) > 6:
            L.append("- …（其余 %d 只见完整报告）" % (len(excl) - 6))
    else:
        L.append("- 今日无直接排除标的。")
    L.append("")

    # ④ 回测视角
    L.append("### ④ 回测视角（独立历史样本，%s → %s，n=%d）" % (b_date, b_fwd, n_all))
    L.append("")
    L.append(f"- 回测择优组：**{rl:+.2f}%**（胜率 {win_low:.0f}%，"
             f"跑赢全池基准 {b_all:+.2f}% 约 {rl-b_all:+.2f}%）")
    L.append(f"- 回测观望组：{rw:+.2f}%　回测排除组：{re_:+.2f}%")
    L.append("- ⚠️ **回测仅 1 期样本（%s 名单，非今日 %d 只），不是今日标的的预期收益**。"
             % (b_date, len(low)))
    L.append(f"- ⚠️ 层①（行业资金流，命中 {n_flow_hit} 只 / {n_total}）是六层里权重最大的一层，"
             "却因无历史存档**未参与回测**，有效性未经验证。")
    L.append("")

    # ⑤ 重要边界
    L.append("### ⑤ 重要边界（务必看）")
    L.append("")
    L.append("- 🚫 **不提供买卖点、入场节奏与止损位**；本组仅表示「未触发硬门槛」，买卖自行决策。")
    L.append("- 📄 排除/观望组每只利空公告均附**日期 + 可点击链接**，且标时效（近30日 / 31–90日 / 90日以上），请自行核实。")
    L.append("- 📊 回测数字效力有限（1 期），**不能外推**；攒够 20 期样本再下结论。")
    L.append("- ⚠️ 本报告为技术面机械过滤结果，**不构成任何投资建议**。")
    L.append("")

    # ⑥ 一句话总结
    L.append("### ⑥ 一句话总结")
    L.append("")
    top = low[0] if low else None
    if top:
        L.append("① 最值得看：**%s %s**（%s，%d 分，%s档）—— %s" % (
            top["code"], top["name"], cn_strats(top.get("strats")),
            top.get("score"), top.get("grade"), op_line(top)))
    if len(low) > 1:
        L.append("② 其余 %d 只均 B 档（单项轻微瑕疵），等回踩或资金转向再评估。" % (len(low) - 1))
    if watch:
        L.append("③ 观望 %d 只已触发降级项，不追高；排除 %d 只踩硬门槛，直接跳过。" % (len(watch), len(excl)))
    L.append("④ 回测只 1 期、层①未验证——方向可参考，别当结论。")

    text = "\n".join(L)
    out = os.path.join(HERE, "微信推送-v4风格-%s.md" % date)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("\n=== 已写:", out, "（%d 字符）===" % len(text))
    return out


if __name__ == "__main__":
    main()
