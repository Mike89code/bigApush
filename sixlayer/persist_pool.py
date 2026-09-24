#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 main.py 当日选股结果持久化到 strategy_pick_log，供 sixlayer 六层过滤读取。

为什么需要这一步：
  bigApush 的 `main.py run` 只把选股结果返回 / 推飞书，不会写入
  strategy_pick_log / advice_log；而 sixlayer/bt_common.build_pool 只读这两张表，
  所以之前每日推送一直「今日无候选」。本脚本复用 QuantSystem.run_full() 的选股结果，
  通过 strategy_scorecard.record_picks 写入 strategy_pick_log（selection_date=今日）。

  注意：run_full 内部会先 _smart_update 刷新 K 线，所以本步也承担"刷新数据"职责，
  可替代原本的 `python main.py run` 步骤。
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

DB = os.path.join(REPO, "data", "stock_selection.db")


def today_bj():
    bj = datetime.now(timezone(timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


def main():
    from strategy_scorecard import record_picks
    from main import QuantSystem

    selection_date = today_bj()
    print(">>> persist_pool: selection_date=%s" % selection_date)

    quant = QuantSystem(os.path.join(REPO, "config", "config.yaml"))
    results = quant.run_full()
    if not results:
        print(">>> 当日无选股结果，跳过持久化。")
        return 0

    total = sum(len(v) for v in results.values())
    print(">>> 选股结果: %d 个策略, %d 只" % (len(results), total))

    conn = sqlite3.connect(DB)
    try:
        added = record_picks(conn, results, selection_date)
    finally:
        conn.close()
    print(">>> 已写入 strategy_pick_log（selection_date=%s），新增 %d 条"
          % (selection_date, added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
