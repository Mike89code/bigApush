# -*- coding: utf-8 -*-
"""v4 六层过滤 · 每日一键流水线（供 GitHub Actions 调用）。

流程：
  1. 抓新浪行业资金流 → s6_flow.json（层①当日快照）
  2. bt_run.run(今日)  → bt_<今日>.json（六层过滤 + A/B/C 档）
  3. 若当日无候选（周末/节假日/数据未就绪）→ 跳过推送，exit 0
  4. gen_wechat_push_v5 → 微信推送文案 md（自动算大盘环境 + 昨日回顾）
  5. recommend_track.save → 记录今日"相对择优"组，供明日回顾
  6. push_send         → 经 Server酱 推送到微信（需 SERVERCHAN_KEY）

用法：
    SERVERCHAN_KEY=xxx python run_v4.py
"""
import os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s6_bridge
import bt_run
import gen_wechat_push_v5
import recommend_track
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

    # 3) 生成文案（v5：内部自动算大盘环境 + 昨日推荐回顾）
    md = gen_wechat_push_v5.main()
    if not md or not os.path.exists(md):
        print("!! 文案生成失败，未推送。")
        return 0

    # 3.5) 记录今日"相对择优"组到 last_recommend.json（供明日回顾板块）
    #      顺序在后：先生成（此时读到的还是昨日记录），再覆盖为今日
    try:
        low = (cur or {}).get("groups", {}).get("low", [])
        picks = [{"code": d.get("code"), "name": d.get("name"),
                  "score": d.get("score")} for d in low]
        recommend_track.save(date, picks)
        print(">>> 已记录今日推荐 %d 只（供明日回顾）" % len(picks))
    except Exception as e:
        print("!! 记录今日推荐失败（不影响本次推送）：%s" % e)

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
