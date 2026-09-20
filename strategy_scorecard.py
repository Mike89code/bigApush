#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略成绩单 —— 记录每天的选股，回看 T+1/T+3/T+5/T+10 的真实涨跌，统计每个策略的胜率。

设计原则：
  1. 只读 stock_kline 算收益，不依赖任何实时接口 → 可复现、断网也能算；
  2. 固定持有期（T+N 交易日），所有策略用同一把尺子，才能横向比；
  3. 除了胜率，同时给「平均收益」和「同期大盘基准」——
     只看胜率会被骗：胜率 60% 但平均收益为负的策略照样亏钱；
     而且大盘普涨时"胜率高"可能只是大盘的功劳，必须减掉基准才看得出真本事。

自己建一张表，不动项目原有表（原表有 UNIQUE(stock_code, selection_date)，
一天一只股票只能一行，做不了"每个策略各自的表现"）。
"""
import random
import sqlite3
from datetime import datetime

HORIZONS = (1, 3, 5, 10)
BENCH_SAMPLE = 300
BENCH_SEED = 42

DDL = """
CREATE TABLE IF NOT EXISTS strategy_pick_log (
    strategy_key   TEXT NOT NULL,
    stock_code     TEXT NOT NULL,
    stock_name     TEXT,
    selection_date TEXT NOT NULL,
    entry_price    REAL,
    recorded_at    TEXT,
    PRIMARY KEY (strategy_key, stock_code, selection_date)
)
"""


# ---------------------------------------------------------------- 记录
def ensure_table(conn):
    conn.execute(DDL)
    conn.commit()


def _latest_close(conn, code, on_or_before):
    return conn.execute(
        "SELECT date, close FROM stock_kline WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, on_or_before),
    ).fetchone()


def record_picks(conn, results, selection_date, max_stale_days=12, log=print):
    """把选股结果写进成绩单表。

    results 形如 {策略key: [{'code': '000001', 'name': '平安银行', ...}, ...]}
    同一天同一策略同一只股票只记一条（重复跑不会重复计数）。

    ⚠️ selection_date 一律用「本次选股的目标日期」，不要用该股票 K 线里的最新日期——
       停牌/退市股的最新 K 线可能是一年前，那样算出来的持有期收益是垃圾，
       会把整份成绩单带偏。数据比目标日期旧超过 max_stale_days 天的直接丢弃。
    """
    ensure_table(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ref = datetime.strptime(str(selection_date)[:10], "%Y-%m-%d").date()
    except Exception:
        ref = datetime.now().date()
        selection_date = ref.strftime("%Y-%m-%d")

    added = 0
    skipped_stale = 0
    for strat, items in (results or {}).items():
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if not code:
                continue
            hit = _latest_close(conn, code, selection_date)
            if not hit:
                continue
            try:
                gap = (ref - datetime.strptime(hit[0][:10], "%Y-%m-%d").date()).days
            except Exception:
                gap = 0
            if gap > max_stale_days:
                skipped_stale += 1
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO strategy_pick_log"
                " (strategy_key, stock_code, stock_name, selection_date, entry_price, recorded_at)"
                " VALUES (?,?,?,?,?,?)",
                (strat, code, it.get("name") or "", selection_date, float(hit[1]), now),
            )
            added += cur.rowcount
    conn.commit()
    msg = f"[成绩单] 已记录 {added} 条选股（已有的自动跳过）"
    if skipped_stale:
        msg += f"，丢弃 {skipped_stale} 条（K线数据过旧，疑似停牌/退市）"
    log(msg)
    return added


# ---------------------------------------------------------------- 取价格序列
def _load_series(conn, codes, batch=400):
    """{code: (dates, closes)} 按日期升序。只取需要的股票，省内存。"""
    out = {}
    codes = [c for c in set(codes)]
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        q = ",".join("?" * len(chunk))
        for code, date, close in conn.execute(
            f"SELECT code, date, close FROM stock_kline WHERE code IN ({q})"
            " ORDER BY code ASC, date ASC",
            chunk,
        ):
            if close is None:
                continue
            d, c = out.setdefault(code, ([], []))
            d.append(date)
            c.append(close)
    return out


def _forward_returns(dates, closes, sel_date, horizons=HORIZONS):
    """从 sel_date 之后第 N 个交易日算收益。返回 {N: 收益率}，未到期的 N 不出现在结果里。"""
    # 找到选入日在序列里的位置（用 <= 的最后一个，防节假日错位）
    idx = None
    for i in range(len(dates) - 1, -1, -1):
        if dates[i] <= sel_date:
            idx = i
            break
    if idx is None:
        return {}
    base = closes[idx]
    if not base:
        return {}
    out = {}
    for n in horizons:
        j = idx + n
        if j < len(closes):
            out[n] = closes[j] / base - 1.0
    return out


# ---------------------------------------------------------------- 统计
def collect(conn, lookback_days=60, horizons=HORIZONS):
    """汇总成绩单数据。返回结构化 dict。"""
    rows = conn.execute(
        "SELECT DISTINCT selection_date FROM strategy_pick_log"
        " ORDER BY selection_date DESC LIMIT ?",
        (lookback_days,),
    ).fetchall()
    dates = [r[0] for r in rows]
    if not dates:
        return {"empty": True}

    picks = conn.execute(
        "SELECT strategy_key, stock_code, selection_date, entry_price FROM strategy_pick_log"
        " WHERE selection_date >= ?",
        (min(dates),),
    ).fetchall()

    series = _load_series(conn, [p[1] for p in picks])

    per_strategy = {}   # {strat: {h: [ret, ...]}}
    total = 0
    matured = 0
    for strat, code, sel_date, _ in picks:
        total += 1
        ser = series.get(code)
        if not ser:
            continue
        rets = _forward_returns(ser[0], ser[1], sel_date, horizons)
        if not rets:
            continue
        matured += 1
        bucket = per_strategy.setdefault(strat, {})
        for h, v in rets.items():
            bucket.setdefault(h, []).append(v)

    # 基准：固定抽样的全市场平均收益（同日期、同持有期）
    all_codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_kline")]
    random.Random(BENCH_SEED).shuffle(all_codes)
    bench_series = _load_series(conn, all_codes[:BENCH_SAMPLE])
    bench = {h: [] for h in horizons}
    for ser in bench_series.values():
        for d in dates:
            rets = _forward_returns(ser[0], ser[1], d, horizons)
            for h, v in rets.items():
                bench[h].append(v)

    def summ(vals):
        if not vals:
            return None
        n = len(vals)
        return {
            "n": n,
            "win": sum(1 for v in vals if v > 0) / n,
            "avg": sum(vals) / n,
            "med": sorted(vals)[n // 2],
            "best": max(vals),
            "worst": min(vals),
        }

    strategies = []
    for strat, bucket in per_strategy.items():
        strategies.append((strat, {h: summ(v) for h, v in bucket.items()}))
    # 按 T+5 平均收益排序（没有 T+5 的排最后）
    strategies.sort(
        key=lambda kv: (kv[1].get(5) or {}).get("avg", -9.9), reverse=True
    )

    return {
        "empty": False,
        "dates": dates,
        "total": total,
        "matured": matured,
        "pending": total - matured,
        "strategies": strategies,
        "bench": {h: summ(v) for h, v in bench.items()},
    }


# ---------------------------------------------------------------- 出报告
def _pct(x):
    return f"{x * 100:+.2f}%" if x is not None else "  –  "


def format_report(data, display_names=None, top_n=8, horizons=(1, 5)):
    """生成可推送的纯文本。"""
    display_names = display_names or {}
    if data.get("empty"):
        return ("━━━━━━━━━━━━━━━━━━━━\n"
                "📊 策略成绩单\n  还没有历史记录，从今天开始积累。\n")

    lines = ["━━━━━━━━━━━━━━━━━━━━", "📊 策略成绩单"]
    lo, hi = min(data["dates"]), max(data["dates"])
    lines.append(f"  统计区间: {lo} ~ {hi}（{len(data['dates'])} 个交易日）")
    lines.append(f"  选股样本: {data['total']} 条 | 已到期可评估: {data['matured']} 条"
                 f" | 待观察: {data['pending']} 条")
    lines.append("")

    bs = data["bench"]
    bh = " | ".join(f"T+{h} 大盘均值 {_pct((bs.get(h) or {}).get('avg'))}"
                    for h in horizons if bs.get(h))
    if bh:
        lines.append(f"  基准（全市场抽样{BENCH_SAMPLE}只）: {bh}")
        lines.append("")

    ranked = [(s, m) for s, m in data["strategies"] if any(m.get(h) for h in horizons)]
    if not ranked:
        lines.append("  已记录选股，但持有期还没走完，暂时无法评估。")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append(f"  ── 按 T+5 平均收益排名（前 {min(top_n, len(ranked))} 名）──")
    for strat, m in ranked[:top_n]:
        name = display_names.get(strat, strat)
        seg = [f"  【{name}】"]
        for h in horizons:
            s = m.get(h)
            if s:
                seg.append(f"T+{h}: {s['n']}样本 胜率{s['win']*100:.0f}% "
                           f"均值{_pct(s['avg'])}")
        lines.append("")
        lines.append(seg[0])
        for part in seg[1:]:
            lines.append("    " + part)

    if len(ranked) > top_n:
        lines.append("")
        lines.append(f"  ── 其余 {len(ranked) - top_n} 个策略 ──")
        for strat, m in ranked[top_n:]:
            name = display_names.get(strat, strat)
            s = m.get(5) or m.get(1)
            if s:
                lines.append(f"  【{name}】T+5 胜率{s['win']*100:.0f}% 均值{_pct(s['avg'])}（{s['n']}样本）")

    lines.append("")
    lines.append("  ⚠️ 样本少（<10）时胜率波动极大，别急着下结论。")
    lines.append("  ⚠️ 均值要和大盘基准比：跑不过大盘的策略没有意义。")
    return "\n".join(lines) + "\n"


def build(conn, lookback_days=60, display_names=None, horizons=(1, 5)):
    """一步到位：统计 + 出文本。"""
    data = collect(conn, lookback_days=lookback_days)
    return format_report(data, display_names=display_names, horizons=horizons), data


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    db = sys.argv[1] if len(sys.argv) > 1 else "data/stock_selection.db"
    conn = sqlite3.connect(db)
    text, data = build(conn)
    print(text)
