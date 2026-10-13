# -*- coding: utf-8 -*-
"""数据就绪闸门 + 自愈（v5.7 新增）。

背景：Eastmoney push2 系（push2his / push2delay）在本机网络出口被定向阻断
（RemoteDisconnected / EOF，2026-09-21 起实测全挂），go-stock 与旧通道
init_full_data 都打这个域 → sqlite 无当日 K 线 → 六层过滤候选池为空 →
推送被静默跳过，只剩一份"建议检查网络"的报告。

本模块把"没数据"从事故变成自动处理：
  check()  体检：当日 K 线覆盖数 / 候选池 / 最近交易日 / 今天是否交易日
  heal()   自愈：腾讯快车道补当日 K 线 → 覆盖率不足时新浪日K兜底 →
            仍失败则明确陈述卡点（只报告已执行的动作，不给"建议"）

调用方：
  - run_v4.py       主流程闸门（CI 场景）
  - autofix_push.py 一键自愈（本地/手动场景）
"""
import os
import sqlite3
import sys
import time
import json
import urllib.request
import ssl
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DB = os.path.join(REPO, "data", "stock_selection.db")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Referer": "https://finance.sina.com.cn"}


def today_bj():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 体检
def check(db_path=None, date=None):
    """返回数据状态 dict。不抛异常。"""
    db_path = db_path or DB
    date = date or today_bj()
    out = {"date": date, "db": db_path, "db_exists": os.path.exists(db_path)}
    if not out["db_exists"]:
        out["error"] = "数据库不存在"
        return out
    try:
        c = sqlite3.connect(db_path)
        out["kline_latest"] = c.execute(
            "select max(date) from stock_kline").fetchone()[0]
        out["kline_today"] = c.execute(
            "select count(*) from stock_kline where date=?", (date,)).fetchone()[0]
        out["kline_total"] = c.execute(
            "select count(*) from stock_kline").fetchone()[0]
        try:
            out["pool_today"] = c.execute(
                "select count(*) from strategy_pick_log where selection_date=?",
                (date,)).fetchone()[0]
            out["pool_latest"] = c.execute(
                "select max(selection_date) from strategy_pick_log").fetchone()[0]
        except sqlite3.Error:
            out["pool_today"], out["pool_latest"] = None, None
        c.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    # 今天是否交易日：由 heal 的快车道统计回填；此处只能看K线
    out["kline_ready"] = (out.get("kline_latest") == date
                          and (out.get("kline_today") or 0) > 0)
    out["pool_ready"] = (out.get("pool_today") or 0) > 0
    return out


# ---------------------------------------------------------------- 腾讯快车道
def _fastlane(date, max_stocks=None):
    """调 utils/tencent_fastlane 补当日K线。返回 (stats, err)。"""
    try:
        from utils.tencent_fastlane import fast_update_today
        stats = fast_update_today(max_stocks=max_stocks, trade_date=date)
        return stats, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 新浪日K兜底
def _sina_one(code, n=5):
    """新浪日K最近n根。返回 [(date, open, high, low, close, volume股), ...] 或 None。"""
    pre = "sh" if code.startswith(("6", "9")) else ("bj" if code[0] in "4" else "sz")
    u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
         "?symbol=%s%s&scale=240&ma=no&datalen=%d" % (pre, code, n))
    try:
        req = urllib.request.Request(u, headers=_H)
        with urllib.request.urlopen(req, timeout=12, context=_ctx) as r:
            t = r.read().decode("utf-8", "replace")
        rows = json.loads(t)
        return [(r["day"], float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"]), int(r["volume"]))
                for r in rows]
    except Exception:
        return None


def sina_backfill(db_path=None, date=None, gap=None, gap_sleep=0.12):
    """对快车道没补上的股票，用新浪日K补当日那一根。

    只补 gap 中列出的 code（快车道 failed 的）。停牌股（not_today）不补——
    停牌本来就没有当日K线，补了反而是假数据。
    返回 (filled, tried)。
    """
    db_path = db_path or DB
    date = date or today_bj()
    codes = list(gap or [])
    filled = 0
    if not codes:
        return 0, 0
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for i, code in enumerate(codes):
                rows = _sina_one(code)
                if not rows:
                    continue
                hit = [r for r in rows if r[0] == date]
                if not hit:
                    continue  # 今天没这根（停牌/未上市），跳过
                d, o, h, l, c, v = hit[-1]
                conn.execute(
                    "DELETE FROM stock_kline WHERE code=? AND date=?", (code, d))
                conn.execute(
                    "INSERT INTO stock_kline (code,date,open,high,low,close,volume,"
                    "market_cap,K,D,J) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
                    (code, d, o, h, l, c, v // 100))  # 股→手，与项目口径一致
                filled += 1
                if gap_sleep and (i + 1) % 10 == 0:
                    time.sleep(gap_sleep)
    finally:
        conn.close()
    return filled, len(codes)


# ---------------------------------------------------------------- 自愈
def heal(db_path=None, date=None, sina_backfill_enabled=True):
    """执行自愈，返回动作清单。

    动作依次：
      1. 腾讯快车道（qt.gtimg.cn 收盘快照，1-2分钟/全市场）
      2. 覆盖率不足 → 新浪日K兜底（只补快车道失败的code）
    返回 dict: {date, before, actions:[...], after, is_trading_day}
    """
    db_path = db_path or DB
    date = date or today_bj()
    before = check(db_path, date)
    acts = []

    # --- 动作1：腾讯快车道
    stats, err = _fastlane(date)
    if err:
        acts.append({"action": "腾讯快车道 qt.gtimg.cn 补当日K线", "ok": False,
                     "detail": err})
    else:
        total = stats.get("total") or 0
        upd = stats.get("updated") or 0
        acts.append({
            "action": "腾讯快车道 qt.gtimg.cn 补当日K线", "ok": bool(stats.get("ok")),
            "detail": "更新 %d/%d 只，失败 %d，非今日快照 %d，耗时 %ss"
                      % (upd, total, stats.get("failed") or 0,
                         stats.get("not_today") or 0, stats.get("elapsed"))})
        is_trading = upd > 0

        # --- 动作2：覆盖率不足且启用了新浪兜底 → 补快车道失败的那些
        if sina_backfill_enabled and total and upd < total * 0.9:
            c = sqlite3.connect(db_path)
            have = {r[0] for r in c.execute(
                "select distinct code from stock_kline where date=?", (date,))}
            allc = [r[0] for r in c.execute(
                "select distinct code from stock_kline order by code")]
            c.close()
            gap = [x for x in allc if x not in have]
            # 排除停牌嫌疑：快车道 not_today 的股票不补（无当日数据属正常）
            filled, tried = sina_backfill(db_path, date, gap=gap)
            acts.append({
                "action": "新浪日K兜底补当日K线", "ok": filled > 0,
                "detail": "缺口 %d 只，尝试 %d 只，补上 %d 只（停牌/未上市不补）"
                          % (len(gap), tried, filled)})

    after = check(db_path, date)
    out = {"date": date, "before": before, "actions": acts, "after": after,
           "is_trading_day": bool(stats and (stats.get("updated") or 0) > 0)}
    return out


def fmt_report(r):
    """把 heal 结果格式化成'执行清单'（陈述已做的事，不给建议）。"""
    L = []
    L.append("【数据自愈报告】%s" % r["date"])
    b, a = r["before"], r["after"]
    L.append("  自愈前：K线最新 %s（当日 %d 根），候选池 %s 条"
             % (b.get("kline_latest"), b.get("kline_today") or 0,
                b.get("pool_today")))
    for x in r["actions"]:
        mark = "✅" if x["ok"] else "❌"
        L.append("  %s %s → %s" % (mark, x["action"], x["detail"]))
    L.append("  自愈后：K线最新 %s（当日 %d 根），候选池 %s 条"
             % (a.get("kline_latest"), a.get("kline_today") or 0,
                a.get("pool_today")))
    L.append("  今天是否交易日：%s" % ("是" if r["is_trading_day"] else "否/未确认"))
    return "\n".join(L)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else today_bj()
    r = heal(date=d)
    print(fmt_report(r))
