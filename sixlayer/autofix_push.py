#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键自愈：没推送？跑这个，它自己修。

用法：
    python sixlayer/autofix_push.py            # 自愈今天
    python sixlayer/autofix_push.py 2026-10-12 # 自愈指定日期
    python sixlayer/autofix_push.py --status   # 只看状态，不动手

流程（全自动，无需人盯）：
  1. data_gate.check   体检：当日K线 / 候选池 / 最近交易日
  2. data_gate.heal    自愈K线：腾讯快车道 → 新浪日K兜底
  3. persist_pool      候选池为空时重跑选股持久化（本地计算）
  4. run_v4            六层过滤 → 生成文案 → 推送（有 SERVERCHAN_KEY 才推）
每步结果都会打印；全部失败才非零退出，且退出信息是"已执行了哪些动作"，
而不是"建议您检查网络"。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import data_gate


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    date = args[0] if args else data_gate.today_bj()

    if "--status" in sys.argv:
        st = data_gate.check(date=date)
        print("【状态】%s" % date)
        for k in ("kline_latest", "kline_today", "pool_latest", "pool_today"):
            print("  %-14s %s" % (k, st.get(k)))
        print("  K线就绪: %s  候选池就绪: %s"
              % (st.get("kline_ready"), st.get("pool_ready")))
        return 0

    print("=" * 60)
    print("一键自愈 · %s" % date)
    print("=" * 60)

    # 1+2) 体检 + K线自愈
    st = data_gate.check(date=date)
    if not st.get("kline_ready"):
        r = data_gate.heal(date=date)
        print(data_gate.fmt_report(r))
        st = data_gate.check(date=date)
    else:
        print(">>> K线已就绪（当日 %d 根），跳过补数" % (st.get("kline_today") or 0))

    # 3) 候选池为空 → 重跑选股持久化
    if not st.get("pool_ready"):
        print(">>> 候选池为空，重跑选股持久化（persist_pool）...")
        try:
            import persist_pool
            persist_pool.main()
        except Exception as e:
            print("!! persist_pool 失败：%s: %s" % (type(e).__name__, e))
            return 1
    else:
        print(">>> 候选池已就绪（当日 %d 条）" % st.get("pool_today"))

    # 4) 六层过滤 + 文案 + 推送
    print(">>> 进入六层过滤主流程（run_v4）...")
    import run_v4
    rc = run_v4.main()
    print("=" * 60)
    print("自愈流程结束，run_v4 退出码 %d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
