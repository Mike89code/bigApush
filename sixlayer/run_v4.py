# -*- coding: utf-8 -*-
"""v4 六层过滤 · 每日一键流水线（供 GitHub Actions 调用）。

流程：
  1. 抓新浪行业资金流 → s6_flow.json（层①当日快照）
  2. bt_run.run(今日)  → bt_<今日>.json（六层过滤 + A/B/C 档）
  3. 若当日无候选（周末/节假日/数据未就绪）→ 跳过推送，exit 0
  4. gen_wechat_push   → 微信推送文案 md
  5. push_send         → 经 Server酱 推送到微信（需 SERVERCHAN_KEY）

用法：
    SERVERCHAN_KEY=xxx python run_v4.py
"""
import os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s6_bridge
import bt_run
import gen_wechat_push
import push_send


def today_bj():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


def main():
    key = (os.environ.get("SERVERCHAN_KEY") or "").strip()
    date = today_bj()
    print(">>> v4 六层过滤 · %s" % date)

    # 1) 行业资金流（层①当日快照）
    s6_bridge.write_flow()

    # 2) 六层过滤（当日，不回测）
    cur = bt_run.run(date, label="当期")
    total = (cur or {}).get("total", 0)
    if not cur or total == 0:
        print(">>> 今日无候选（%s），跳过推送。" % date)
        return 0

    # 3) 生成文案
    md = gen_wechat_push.main()
    if not md or not os.path.exists(md):
        print("!! 文案生成失败，未推送。")
        return 0

    # 4) 推送
    if not key:
        print("!! 未配置 SERVERCHAN_KEY，文案已生成但未推送：%s" % md)
        return 0
    code, msg = push_send.send(key, md)
    print(msg)
    if code == 0:
        print("✅ 推送成功")
        return 0
    print("!! 推送失败（code=%s）" % code)
    return 1


if __name__ == "__main__":
    sys.exit(main())
