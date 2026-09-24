#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「v5 六层过滤」风格微信推送文案（在 v4 基础上按评审大幅改进）。

评审改进落地：
  · 大盘环境判定（四大指数涨跌幅→评分→仓位建议；暴跌日发空仓简报）
  · 昨日推荐回顾（跨日跟踪入选标的真实涨跌，建信任闭环）
  · 决策卡（3 秒抓重点）
  · 信息优先级重排：决策卡→回顾→今日首选→观察池→坚决避开→可以看→大盘复盘→策略成绩单→要闻→清单
  · 风控参数（止损=5日线 / 目标=前高 / 仓位按评分分档）
  · 双策略共振标注
  · 压力位 <2% 才标 ⚠️（有区分度）
  · C 级/低分票拆「观察池」，不与 A/B 混排
  · 样本<10 的策略合并一行，不占表格
  · 财经要闻 3 条（新浪 7x24，带影响点评）
  · 清单压缩（emoji 伪表格，适配微信）

数据源同 v4：当期 bt_<YYYYMMDD>.json + 回测基线 bt_backtest_baseline.json。
大盘环境与昨日回顾可由调用方注入（env / review），便于本地预览；为 None 时自动计算。

用法：python gen_wechat_push_v5.py  → 写 sixlayer/微信推送-v5风格-<date>.md
"""
import os
import json
import glob
import datetime
import statistics

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

MIN_SAMPLE = 10  # 策略成绩单参与排名的最小样本数


def _pos_tier(score):
    """个股仓位按评分分档（总量另看大盘评分给的仓位）。"""
    if score is None:
        return "≤1成"
    if score >= 85:
        return "≤2成"
    if score >= 70:
        return "≤1.5成"
    if score >= 60:
        return "≤0.5成"
    return "观察"


def cn_strats(lst):
    return "/".join(STRAT_CN.get(s, s) for s in (lst or [])) or "无"


def sig(v, nd=2):
    return "—" if v is None else ("%." + str(nd) + "f") % v


def grade_flaws(d):
    """减分项（渲染成单票 ⚠️ 行）。压力位仅 <2% 才算"近"，保证区分度。"""
    flaws = []
    ann = d.get("ann") or {}
    if ann.get("S3"):
        t3 = (ann["S3"][0].get("title") or "")[:24]
        # 减持结果/完成/终止类 = 悬着的剑落地，利空出尽而非新增利空
        note = "（已执行完毕，利空出尽）" if any(
            k in t3 for k in ("结果", "完成", "完毕", "终止", "届满")) else ""
        flaws.append("三级公告：%s%s" % (t3, note))
    if (d.get("avg_amt20_wan") or 0) < 8000:
        flaws.append("日均成交%.1f亿偏小" % ((d.get("avg_amt20_wan") or 0) / 1e4))
    if (d.get("rsi") or 0) > 70:
        flaws.append("RSI%.1f偏高" % d["rsi"])
    rd = d.get("res_dist_pct")
    if rd is not None and rd < 2:
        flaws.append("距前高压力位仅%.1f%%" % rd)
    sn = d.get("sector_net_yi")
    if sn is not None and sn < 0:
        flaws.append("行业当日净流出%.1f亿" % abs(sn))
    return flaws


def _flow_txt(d):
    sn = d.get("sector_net_yi")
    if sn is None:
        return "行业资金缺失"
    return ("行业净流入 %+.1f亿" % sn) if sn > 0 else ("行业净流出 %.1f亿" % abs(sn))


def _momentum_txt(d):
    """超额收益 + 量比（分形态解读，结论直接给出）。"""
    parts = []
    ex20 = d.get("rs_excess")
    if ex20 is not None:
        parts.append("20日超额 %s%spp" % ("+" if ex20 >= 0 else "", sig(ex20, 1)))
    from bt_common import vol_check
    v, vtxt = vol_check(d)
    if vtxt:
        parts.append(vtxt)
    return " ｜ ".join(parts)


def _stop_txt(d):
    """止损按板宽：创业板/科创板(30/68) 20cm 板用 MA10（更宽）；主板用 MA5。"""
    close = d.get("close")
    if not close:
        return None
    board20 = (d.get("code") or "").startswith(("30", "68"))
    ma = d.get("ma10") if board20 else d.get("ma5")
    if not ma:
        return None
    return "止损 %s(%s%%，%s)" % (
        sig(ma), sig((ma / close - 1) * 100, 1),
        "20cm板·MA10下" if board20 else "MA5下")


def _today_bj():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


def _load_current():
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


def _strat_stats(bt):
    """按策略聚合：{strat: {n, win, mean, names}}（回测视角，用 fwd_ret）。"""
    out = {}
    for d in bt.get("items", []):
        for s in (d.get("strats") or []):
            b = out.setdefault(s, {"rets": [], "names": []})
            if d.get("fwd_ret") is not None:
                b["rets"].append(d["fwd_ret"])
            b["names"].append(d.get("name"))
    res = {}
    for s, b in out.items():
        rs = b["rets"]
        n = len(rs)
        if n:
            win = sum(1 for x in rs if x > 0) / n * 100
            mean = statistics.mean(rs)
            wins = [x for x in rs if x > 0]
            losses = [x for x in rs if x < 0]
            plr = (statistics.mean(wins) / abs(statistics.mean(losses))) if (wins and losses) else None
            pf = (sum(wins) / abs(sum(losses))) if (wins and losses) else None
        else:
            win = mean = 0.0
            plr = pf = None
        res[s] = {"n": n, "win": win, "mean": mean, "plr": plr, "pf": pf}
    return res


def risk_reward(d):
    """盈亏比：目标①减半 + 目标②清仓的加权平均盈利 / 止损幅度 → (rr, 目标①, 目标②)。"""
    close = d.get("close")
    t1 = d.get("res_price")
    if not close or not t1:
        return None, None, None
    board20 = (d.get("code") or "").startswith(("30", "68"))
    st = d.get("ma10") if board20 else d.get("ma5")
    if not st:
        return None, None, None
    risk = abs((close - st) / close * 100)
    t2 = d.get("meas_target") or t1
    gain1 = (t1 / close - 1) * 100
    gain2 = (t2 / close - 1) * 100
    gain_avg = (gain1 + gain2) / 2.0   # 半仓到①、半仓到②
    rr = round(gain_avg / risk, 2) if risk > 0 else None
    return rr, t1, t2


def _warn_flags(d):
    """⚠️ 警告旗标清单（>=2 项叠加 → 不得进入操作计划）。"""
    flags = list(grade_flaws(d))
    from bt_common import vol_check
    v, vtxt = vol_check(d)
    if v in ("warn", "bad"):
        flags.append(vtxt.split(" ")[0] + vtxt.split(" ", 1)[1] if " " in vtxt else vtxt)
    rr, _, _ = risk_reward(d)
    if rr is not None and rr < 1.5:
        flags.append("盈亏比1:%.2f不足1.5" % rr)
    return flags


def _plan_block(cands):
    """明日操作计划（准入制）：盈亏比≥1.5 且 ⚠️<2 项才可入计划，其余给条件单。"""
    admitted, rejected = [], []
    for d in cands:
        flags = _warn_flags(d)
        rr, t1, t2 = risk_reward(d)
        ok = (rr is not None and rr >= 1.5) and len(flags) < 2
        (admitted if ok else rejected).append((d, flags, rr))
    if not admitted and not rejected:
        return []
    L = ["🎯 **明日操作计划**（开盘竞价后核对，大幅高开/低开则本计划作废）", ""]
    for d, _fl, _rr in admitted[:2]:
        close = d.get("close")
        if not close:
            continue
        board20 = (d.get("code") or "").startswith(("30", "68"))
        stop_px = d.get("ma10") if board20 else d.get("ma5")
        stop_tag = "MA10" if board20 else "MA5"
        buy_lo = close * 0.99
        buy_hi = close * 1.015
        chase = close * 1.025
        tier = _pos_tier(d.get("score"))
        L.append("**%s %s**（%s分，现价 %s）：" % (
            d.get("code"), d.get("name"), d.get("score") or "—", sig(close)))
        L.append("   开盘 %s~%s（%s%%~%s%%）→ 可介入，首笔 0.5成" % (
            sig(buy_lo), sig(buy_hi),
            sig((buy_lo / close - 1) * 100, 1), sig((buy_hi / close - 1) * 100, 1)))
        L.append("   高开 >%s（+%s%%）→ 不追，等回踩 ｜ 低开 <%s（%s下方）→ 放弃" % (
            sig(chase), sig((chase / close - 1) * 100, 1),
            sig(stop_px) if stop_px else "止损位", stop_tag))
        L.append("   （介入后上限 %s仓，止损 %s）" % (tier, sig(stop_px) if stop_px else "—"))
    if admitted:
        L.append("首笔 0.5 成试探、次日站稳再加，单票上限见各自仓位档；以上为机械推演，不构成投资建议。")
    else:
        L.append("今日无票通过计划准入（盈亏比/警告叠加拦截），空仓等待。")
    if rejected:
        L.append("❌ **未入计划**（识别到风险，硬规则拦截）：")
        for d, flags, rr in rejected[:2]:
            res = d.get("res_price")
            L.append("   %s %s：⚠️×%d（%s）" % (
                d.get("code"), d.get("name"), len(flags), "；".join(flags)[:60]))
            if res:
                L.append("   重新入计划条件：站上 %s 且盈亏比修复至 1:1.5 以上" % sig(res))
    L.append("")
    L.append("📌 计划作废后的动作：")
    L.append("   若全部首选高开 >2.5% → 今日不追，等明日重新出信号")
    L.append("   若大盘开盘跌 >1.5% → 全部计划作废，直接观望")
    L.append("")
    return L


def _env_card(env):
    if not env:
        return ["📉 大盘环境：数据获取失败（仅供参考）"]
    lines = []
    lines.append("📉 大盘：%s（%s，均 %s）" % (
        env["mood"], env["range"], sig(env["avg_pct"]) + "%"))
    lines.append("   大盘评分 %d/100 ｜ 建议仓位 %s" % (env["score"], env["position"]))
    return lines


def _review_block(review):
    """回顾明细（标题由 main 的 h3 承担，避免重复）。"""
    if not review or not review.get("items"):
        return ["暂无历史推荐可回顾（明日起自动累计）"]
    items = review["items"]
    ok = [x for x in items if x.get("ret") is not None and x["ret"] >= 1.5]
    bad = [x for x in items if x.get("ret") is not None and x["ret"] <= -3.0]
    L = []
    for x in items[:8]:
        ret = x.get("ret")
        if ret is None:
            tag = "—（无K线数据，人工核查）"
        elif ret >= 1.5:
            tag = "✅ +%.2f%% 达标（可止盈或留观察）" % ret
        elif ret <= -3.0:
            tag = "❌ %.2f%% 触发止损（执行离场）" % ret
        else:
            tag = "➖ %+.2f%% 未触及止损（继续持有/观察）" % ret
        L.append("   %s %s（%s分）：%s" % (
            x.get("code"), x.get("name"), x.get("score") or "—", tag))
    L.append("   小结：达标 %d ／ 止损 %d ／ 共 %d 只" % (len(ok), len(bad), len(items)))
    return L


def main(env=None, review=None, cur=None, news=None):
    if cur is None:
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

    # 自动算大盘环境 / 昨日回顾（未注入时）
    if env is None:
        try:
            from market_env import get_env
            env = get_env()
        except Exception:
            env = None
    if review is None:
        try:
            from recommend_track import load_prev, review as _do_review
            from bt_common import DB
            prev = load_prev()
            review = _do_review(prev, DB) if prev else None
        except Exception:
            review = None
    if news is None:
        try:
            from market_news import fetch_top3
            news = fetch_top3()
        except Exception:
            news = None

    crash = bool(env and env.get("crash"))
    weak = bool(env and not crash and env.get("score", 100) < 45)

    # 计划准入（提前算，决策卡与操作计划统一口径）：评分≥80 的票里
    # 盈亏比≥1.5 且 ⚠️<2 项 → 可入计划；其余降条件单/观察
    pref_cands = [d for d in low if (d.get("score") or 0) >= 80][:5]
    admitted, rejected = [], []
    if not crash:
        for d in pref_cands:
            flags = _warn_flags(d)
            rr, _, _ = risk_reward(d)
            ok = (rr is not None and rr >= 1.5) and len(flags) < 2
            (admitted if ok else rejected).append((d, flags, rr))

    L = []
    # ===== 决策卡 =====
    L.append("🔍 **A股六层过滤 · 选股日报 %s**" % date)
    L.append("")
    L.append("━━━━━━━━━━━━━━━━")
    L.extend(_env_card(env))
    if crash:
        L.append("   ⚠️ 暴跌日（均跌>2%）：今日空仓观望，以下仅复盘")
    L.append("━━━━━━━━━━━━━━━━")
    top = admitted[0][0] if admitted else (low[0] if low else None)
    L.append("💡 今日首选：%s" % (
        ("%s %s（%s分）" % (top["code"], top["name"], top.get("score") or "无评分"))
        if top else "无"))
    L.append("🚫 坚决避开：%d 只" % len(excl))
    L.append("⚠️ 观察不追：%d 只" % len(watch))
    L.append("")
    # 明日操作计划（决策卡之后，昨日回顾之前）
    if not crash:
        try:
            L.extend(_plan_block(pref_cands))
        except Exception:
            pass

    # ===== 昨日推荐回顾（信任闭环，放最前；日期并入标题，避免重复） =====
    _rv_title = "📌 昨日推荐回顾"
    if review and review.get("date"):
        _rv_title += "（%s 入选，今日表现）" % review["date"]
    L.append("### " + _rv_title)
    L.append("")
    L.extend(_review_block(review))
    L.append("")

    # ===== 暴跌日：空仓简报 =====
    if crash:
        L.append("### ⚠️ 今日空仓观望")
        L.append("")
        L.append("大盘暴跌，六层过滤照常产出 %d 只候选，但**弱市硬推票胜率骤降**，" % n_total)
        L.append("今日建议**空仓或≤1成**，把精力放在复盘与明日预案上。")
        L.append("若手痒只看最高胜率标的（非操作建议）：")
        for d in low[:3]:
            L.append("   · %s %s（%s档）" % (
                d["code"], d["name"],
                {"A": "A·无瑕疵", "B": "B·轻微", "C": "C·跟踪"}.get(d.get("grade") or "C", d.get("grade"))))
        L.append("")
        L.append("> 本简报为技术面机械过滤结果，不构成任何投资建议。")
        text = "\n".join(L)
        out = os.path.join(HERE, "微信推送-v5风格-%s.md" % date)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)
        print("\n=== 已写:", out, "（%d 字符）===" % len(text))
        return out

    # ===== 正常 / 弱市：完整版 =====
    # 弱市降级：相对择优只留前 4，隐藏"可以看"
    low_show = low[:4] if weak else low
    if weak:
        L.append("> 🔻 弱势行情（评分<%d）：已隐藏「可以看」组，仅保留最高胜率标的。" % 45)

    # 拆组：首选 = 评分≥80 且通过硬规则准入（最多 5 只）；其余（含被拦截票）进观察池
    pref = [d for d, _f, _r in admitted]
    rejected_map = {d["code"]: flags for d, flags, _rr in rejected}
    obsv = [d for d in low_show if d not in pref]

    # ① 今日首选（放大 + 风控参数，每票 4~6 行）
    L.append("### ① 今日首选（%d 只，评分≥80 且通过硬规则）" % len(pref))
    L.append("")
    if not pref:
        L.append("今日无 A/B 级首选，全部转入观察池。")
        L.append("")
    for d in pref:
        sc = d.get("score")
        fire = "🔥 " if len(d.get("strats") or []) >= 2 else ""
        L.append("%s**%s %s**  %s分  %s级" % (
            fire, d["code"], d["name"],
            sc if sc is not None else "—", d.get("grade") or "C"))
        L.append("   RSI %s ｜ %s ｜ %s" % (
            sig(d.get("rsi"), 1), d.get("industry") or "行业—", _flow_txt(d)))
        mom = _momentum_txt(d)
        if mom:
            L.append("   " + mom)
        st = [STRAT_CN.get(s, s) for s in (d.get("strats") or [])]
        if st:
            L.append("   策略：%s%s" % (
                " × ".join(st), "（双共振）" if len(st) >= 2 else ""))
        stop = _stop_txt(d)
        res = d.get("res_price")
        close = d.get("close")
        rr, t1, t2 = risk_reward(d)
        if stop and close and res:
            t1_txt = "目标① %s(+%s%%) 减半" % (sig(t1), sig((t1 / close - 1) * 100, 1))
            if t2 and t2 != t1:
                t2_txt = "目标② %s(+%s%%) 清仓" % (sig(t2), sig((t2 / close - 1) * 100, 1))
            else:
                t2_txt = "破位清仓"
            rr_txt = " ｜ 盈亏比 1:%s%s" % (
                sig(rr) if rr is not None else "—",
                " ⚠️不足1.5" if (rr is not None and rr < 1.5) else "")
            L.append("   %s → %s → %s ｜ %s仓%s" % (
                stop, t1_txt, t2_txt, _pos_tier(sc), rr_txt))
        elif stop:
            L.append("   %s ｜ %s仓" % (stop, _pos_tier(sc)))
        warns = grade_flaws(d)
        if warns:
            L.append("   ⚠️ %s" % "；".join(warns))
    L.append("")

    # ② 观察池（择优组里评分<80：含强票备选与低分观望）
    if obsv:
        L.append("### ② 观察池（%d 只，评分<80，分批观察）" % len(obsv))
        L.append("")
        for d in obsv:
            sc = d.get("score")
            st = "/".join(STRAT_CN.get(s, s) for s in (d.get("strats") or [])) or "—"
            extra = []
            ex20 = d.get("rs_excess")
            if ex20 is not None:
                extra.append("超额%s%spp" % ("+" if ex20 >= 0 else "", sig(ex20, 1)))
            vr = d.get("vol_ratio")
            if vr is not None:
                extra.append("量比%.2f" % vr)
            L.append("   %s %s  %s分（%s）｜ RSI %s%s ｜ %s" % (
                d["code"], d["name"],
                sc if sc is not None else "—", d.get("grade") or "C",
                sig(d.get("rsi"), 1),
                (" ｜ " + " ｜ ".join(extra)) if extra else "", st))
            if d["code"] in rejected_map:
                L.append("      ⛔ 已被硬规则拦截（⚠️×%d），重新入计划条件：突破压力位且盈亏比修复至 1:1.5"
                         % len(rejected_map[d["code"]]))
        # 备选强票：RS>3pp 且量比>1.5 —— 给触发条件，不提前埋伏
        backups = [d for d in obsv
                   if (d.get("rs_excess") or -99) > 3 and (d.get("vol_ratio") or 0) > 1.5]
        for d in backups[:2]:
            res = d.get("res_price")
            if res:
                L.append("")
                L.append("📋 **备选**：%s %s（超额+%spp、量比%.2f，技术面达标）"
                         % (d["code"], d["name"], sig(d.get("rs_excess"), 1), d.get("vol_ratio")))
                L.append("   触发条件：**站上 %s（突破压力位）** → 介入 0.5成，等突破确认再入首选" % sig(res))
                L.append("   否则继续等，不提前埋伏。")
        L.append("")

    # ③ 坚决避开（提前！）
    L.append("### ③ 坚决避开（%d 只，触发硬门槛：流动性 / 一级利空）" % len(excl))
    L.append("")
    if excl:
        for d in excl[:8]:
            rs = "；".join(d.get("exclude_reasons") or []) or "触发硬门槛"
            L.append("🚫 **%s %s**：%s" % (d["code"], d["name"], rs))
        if len(excl) > 8:
            L.append("🚫 …（其余 %d 只见完整报告）" % (len(excl) - 8))
    else:
        L.append("🚫 今日无直接排除标的。")
    L.append("")

    # ④ 观察名单（技术成立但有减分项 / 行业分散降级，与决策卡"观察不追"同一口径）
    if not weak:
        L.append("### ④ 观察名单（%d 只，不追高）" % len(watch))
        L.append("")
        if watch:
            for d in watch[:8]:
                ws = "；".join(d.get("watch_reasons") or []) or "触发降级项"
                L.append("👀 **%s %s**（%s）：%s" % (
                    d["code"], d["name"], cn_strats(d.get("strats")), ws))
        else:
            L.append("👀 今日无须观望组标的。")
        L.append("")

    # ⑤ 大盘复盘 + 资金流向（与决策卡同一份 env 数据）
    L.append("### ⑤ 大盘复盘 · 资金流向")
    L.append("")
    if env:
        for idx in env["indices"]:
            L.append("   %s %s%s" % (idx["name"], sig(idx["pct"]) + "%",
                                     " 🔴" if idx["pct"] < 0 else " 🟢"))
        adv, dec = env.get("adv"), env.get("dec")
        if adv is not None and dec is not None:
            tone = "普跌" if dec > adv * 2 else ("普涨" if adv > dec * 2 else "分化")
            L.append("   涨跌家数 %d:%d（%s）——家数比指数诚实" % (adv, dec, tone))
    n_low = len(low_show)
    n_out = sum(1 for x in low_show if (x.get("sector_net_yi") or 0) < 0)
    if n_low:
        if n_out:
            L.append("   ⚠️ 择优组 %d 只（首选%d＋观察%d）中，%d 只所属行业"
                     "当日资金净流出，行业层面不配合，注意降仓。"
                     % (n_low, len(pref), len(obsv), n_out))
        else:
            L.append("   ✅ 择优组所属行业当日资金均为净流入，行业配合。")
    L.append("")

    # ⑥ 策略成绩单（组均一行；有效策略单列，样本不足合并一行名单）
    L.append("### ⑥ 策略成绩单（回测视角，%s → %s，n=%d）" % (
        bt.get("date"), bt.get("fwd"), sum(1 for x in bt.get("items", []) if x.get("fwd_ret") is not None)))
    L.append("")
    rl, nl = _mean(bt["groups"]["low"])
    rw, nw = _mean(bt["groups"]["watch"])
    re_, ne = _mean(bt["groups"]["exclude"])
    b_items = [x for x in bt["items"] if x.get("fwd_ret") is not None]
    b_all = statistics.mean([x["fwd_ret"] for x in b_items]) if b_items else 0.0
    win_low = (sum(1 for x in bt["groups"]["low"] if (x.get("fwd_ret") or 0) > 0) / nl * 100) if nl else 0.0
    L.append("择优组 **%s%%**（胜率 %s%%，跑赢全池约 %s%%）｜ 观望组 %s%% ｜ 排除组 %s%%"
             % (sig(rl), "%.0f" % win_low, sig(rl - b_all), sig(rw), sig(re_)))
    # 质量三指标：胜率会骗人，盈亏比/PF 不会
    rets = [x["fwd_ret"] for x in b_items]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    if wins and losses:
        plr = statistics.mean(wins) / abs(statistics.mean(losses))
        pf = sum(wins) / abs(sum(losses))
        worst = min(rets)
        verdict = "✅ 有效" if (pf > 1.5 and win_low >= 50) else ("⚠️ 观察" if pf > 1.0 else "❌ 无效")
        L.append("盈亏比 %s ｜ Profit Factor %s ｜ 最差单票 %s%% ｜ %s"
                 % (sig(plr), sig(pf), sig(worst), verdict))
    L.append("")
    ss = _strat_stats(bt)
    today_by_strat = {}
    for d in I:
        for s in (d.get("strats") or []):
            today_by_strat[s] = today_by_strat.get(s, 0) + 1
    rows = []
    for s, st in ss.items():
        rows.append((s, st["n"], st["win"], st["mean"], today_by_strat.get(s, 0)))
    rows.sort(key=lambda x: -x[3])
    ok_rows = [r for r in rows if r[1] >= MIN_SAMPLE]
    less_rows = [r for r in rows if r[1] < MIN_SAMPLE]
    if ok_rows:
        L.append("✅ 有效策略（样本≥%d）：" % MIN_SAMPLE)
        for s, n, win, mean, tn in ok_rows:
            st = ss.get(s) or {}
            plr, pf = st.get("plr"), st.get("pf")
            extra = ""
            if plr is not None and pf is not None:
                extra = " ｜ 盈亏比 %s ｜ PF %s" % (sig(plr), sig(pf))
                if pf >= 2 and win < 50:
                    extra += " 【高盈亏比型】⭐"
                elif win >= 50 and pf >= 1.5:
                    extra += " 【高胜率型】"
                elif pf < 1.5:
                    extra += " 【⚠️边缘 PF<1.5】"
            L.append("   %s  胜率 %s%%%s  今日%d只" % (
                STRAT_CN.get(s, s), "%.0f" % win, extra, tn))
    if less_rows:
        L.append("⚠️ 样本不足（<%d），暂不排名：%s" % (MIN_SAMPLE, "/".join(
            STRAT_CN.get(s, s) for s, *_ in less_rows)))
    if not ok_rows and not less_rows:
        L.append("（暂无策略样本）")
    L.append("")

    # ⑦ 今日要闻（只留 3 条能映射影响点评的）
    L.append("### ⑦ 今日要闻（影响明日）")
    L.append("")
    if news:
        for x in news:
            L.append("• %s → %s" % (x.get("t"), x.get("c")))
    else:
        L.append("（要闻获取失败，今日略）")
    L.append("")

    # ⑧ 完整清单（压缩）
    L.append("### ⑧ 完整清单（%d 只，code 名称）" % n_total)
    L.append("")
    L.append("相对择优 %d：%s" % (len(low), "  ".join(
        "%s%s" % (d["code"], d["name"]) for d in low)))
    L.append("可以看 %d：%s" % (len(watch), "  ".join(
        "%s%s" % (d["code"], d["name"]) for d in watch)) or "可以看 0")
    L.append("避开 %d：%s" % (len(excl), "  ".join(
        "%s%s" % (d["code"], d["name"]) for d in excl)))
    L.append("")

    # 边界
    L.append("### ⑨ 重要边界")
    L.append("")
    L.append("- 🚫 **不提供买卖点、入场节奏与止损位以外的总仓位建议**；本组仅表示「未触发硬门槛」，买卖自行决策。")
    L.append("- 📊 回测仅 1 期样本，效力有限，**不能外推**；攒够 20 期再下结论。")
    L.append("- ⚠️ 本报告为技术面机械过滤结果，**不构成任何投资建议**。")
    L.append("")
    L.append("> 本回答由AI生成，仅供参考，请仔细甄别，谨慎投资。")

    text = "\n".join(L)
    out = os.path.join(HERE, "微信推送-v5风格-%s.md" % date)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("\n=== 已写:", out, "（%d 字符）===" % len(text))
    return out


if __name__ == "__main__":
    main()
