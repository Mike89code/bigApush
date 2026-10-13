# -*- coding: utf-8 -*-
"""v4 六层过滤 · 每日一键流水线（供 GitHub Actions 调用）。

流程：
  0. 数据闸门（v5.7 新增）：当日K线未就绪 → 自动自愈（腾讯快车道 → 新浪兜底）
  1. 抓新浪行业资金流 → s6_flow.json（层①当日快照）
  2. bt_run.run(今日)  → bt_<今日>.json（六层过滤 + A/B/C 档）
     候选池为空 → 自愈：重跑选股持久化后重试一次
     仍为空：非交易日静默跳过（exit 0）；交易日明确失败（exit 1，让 CI 标红）
  3. gen_wechat_push_v5 → 微信推送文案 md（自动算大盘环境 + 昨日回顾）
  4. recommend_track.save → 记录今日"相对择优"组，供明日回顾
  5. push_send         → 经 Server酱 推送到微信（需 SERVERCHAN_KEY）

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
import data_gate


def today_bj():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%Y-%m-%d")


def data_gate_heal(date):
    """数据闸门：K线不是今天 → 自愈。返回 (status, is_trading_day)。"""
    st = data_gate.check(date=date)
    if st.get("kline_ready"):
        print(">>> 数据闸门：当日K线已就绪（%d 根）" % (st.get("kline_today") or 0))
        return st, True
    print(">>> 数据闸门：当日K线未就绪（最新 %s），执行自愈..."
          % st.get("kline_latest"))
    r = data_gate.heal(date=date)
    print(data_gate.fmt_report(r))
    st = data_gate.check(date=date)
    return st, bool(r.get("is_trading_day"))


def main():
    key = (os.environ.get("SERVERCHAN_KEY") or "").strip()
    date = today_bj()
    print(">>> v4 六层过滤 · %s" % date)

    # 0) 数据闸门（v5.7）：没数据自己修，不把活留给人
    st, is_trading = data_gate_heal(date)

    # 1) 行业资金流（层①当日快照）
    s6_bridge.write_flow()

    # 2) 六层过滤（当日，不回测）
    cur = bt_run.run(date, label="当期")
    total = (cur or {}).get("total", 0)

    # 2.5) 候选池为空 → 自愈：重跑选股持久化（本地计算，不重抓K线），再试一次
    if not cur or total == 0:
        print(">>> 候选池为空，执行自愈：重跑选股持久化（persist_pool）...")
        try:
            import persist_pool
            persist_pool.main()
        except Exception as e:
            print("!! 自愈失败（persist_pool）：%s: %s" % (type(e).__name__, e))
        cur = bt_run.run(date, label="当期-自愈重试")
        total = (cur or {}).get("total", 0)

    if not cur or total == 0:
        # 明确失败：非交易日静默跳过；交易日 exit 1 让 CI 标红（不静默）
        if is_trading and st.get("kline_ready"):
            print("!! 今日为交易日且K线已就绪，但选股候选池仍为空。")
            print("!! 已执行自愈：腾讯快车道补K线 → 重跑选股持久化 → 重试过滤。")
            print("!! 请检查策略选股结果（QuantSystem.run_full 是否返回空）。")
            return 1
        print(">>> 今日无候选（%s，非交易日或数据未就绪），跳过推送。" % date)
        return 0

    # 3) 生成文案（v5：内部自动算大盘环境 + 昨日推荐回顾）
    md = gen_wechat_push_v5.main()
    if not md or not os.path.exists(md):
        print("!! 文案生成失败，未推送。")
        return 1

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
