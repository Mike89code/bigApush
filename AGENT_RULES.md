# AGENT_RULES.md — A股推送链路 · 行动宪章

> 本文件是给 AI（dsh / WorkBuddy / 任何跑这条链路的 agent）的行为约束。
> 核心一句话：**发现链路出问题，动手修，然后汇报你做了什么；不准写"建议"报告。**

---

## 一、铁律（违反任何一条都算失败）

1. **禁止输出"建议您…"式报告。** 不允许出现"建议检查网络连接""建议稍后再试"
   "考虑使用其他数据源或缓存数据""是否需要更新 API 访问方式"这类把活推回给用户的句子。
2. **先执行，后汇报。** 标准动作：跑 `python sixlayer/autofix_push.py`，
   把它打印的执行清单原样汇报，附上关键数字（更新了几只、耗时、退出码）。
3. **修不了才说话，且只说事实。** 全部自愈手段都失败时，报告必须是
   "已执行：A → 失败原因 X；B → 失败原因 Y。卡点：Z"，
   不是"您可以尝试…"。
4. **不准静默跳过。** "今日无候选"必须区分：非交易日（正常）vs 交易日数据坏了（故障）。
   run_v4.py 已在交易日+数据就绪仍无候选时 exit 1，看到退出码 1 就是故障，必须处理。

## 二、标准动作链（按顺序执行，不要跳步）

```
1. python sixlayer/autofix_push.py --status     # 先看状态（只读，安全）
2. python sixlayer/autofix_push.py              # 一键自愈：补K线→选股→过滤→推送
3. python sixlayer/data_gate.py <日期>           # 只补数据不跑选股（单独调试用）
```

autofix_push.py 内部顺序：`check 体检 → heal 补K线（腾讯快车道→新浪兜底）
→ persist_pool 重跑选股 → run_v4 六层过滤+文案+推送`。
全自动，无人值守可跑。

## 三、数据源事实表（2026-10-13 实测，别再浪费时间"检查网络"）

| 端点 | 状态 | 结论 |
|---|---|---|
| push2his / push2delay / push2.eastmoney.com | ❌ RemoteDisconnected / EOF | **本机网络出口被定向封锁，检查网络、换域名、慢速重试全部无效，不要试** |
| qt.gtimg.cn（腾讯实时快照） | ✅ 45s/5292只 | 当日K线主力源（tencent_fastlane） |
| web.ifzq.gtimg.cn（腾讯日K前复权） | ✅ | 指数/个股K线备用 |
| quotes.sina.cn getKLineData（新浪日K） | ✅ | data_gate 二级兜底 |
| vip.stock.finance.sina.com.cn（新浪行业资金流） | ✅ | s6_bridge 在用 |
| emweb.securities.eastmoney.com（F10行业） | ✅ | bt_run.f10_industry |
| np-anotice-stock.eastmoney.com（公告） | ✅ | bt_common.ensure_anns |

go-stock（D:\安装\go-stock）硬编码打 push2 系，EOF 刷屏是已知问题，
它的数据只影响 dsh 工作区看盘，**不影响推送链路**（推送走 bigApush 自己的 sqlite）。
看到 go-stock EOF 日志不要慌，更不要写报告，直接跑上面的自愈链。

## 四、汇报格式（照这个写）

```
【自愈执行报告】2026-10-13
  动作1 腾讯快车道补K线 → ✅ 5292/5292 只，45.1s
  动作2 选股持久化     → ✅ N 个策略 M 只
  动作3 六层过滤       → ✅ 候选 N，相对择优 X / 观望 Y / 排除 Z
  文案                 → ✅ sixlayer/微信推送-v4风格-2026-10-13.md
  推送                 → ⏭ 未配置 SERVERCHAN_KEY（或 ✅ 成功）
```

数字必须来自真实运行输出，不准编。用户会核对评分卡与明细数字是否一致。

## 五、已知边界

- 停牌股当日无K线属正常，新浪兜底不补（补了就是假数据）。
- 候选池为空且是交易日：查 QuantSystem.run_full() 是否返回空（策略问题），
  不是数据问题——这时候该做的是看选股日志，不是换数据源。
- 本地库在 `bigApush/data/stock_selection.db`；CI 库由 actions/cache 传递。
