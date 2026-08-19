# 复利定投策略参考（DCA Manager）

## 1. 默认参数（与网页应用 DEFAULT_CONFIG 一致）

| 参数 | 默认值 | 说明 |
|---|---|---|
| 初始本金 principal | 200,000 ¥ | 资金池起点 |
| 单期定投 periodAmount | 5,000 ¥ | 每个定投日投入金额 |
| 频率 frequency | weekly | daily=每1交易日 / weekly=每5交易日 / monthly=每21交易日 |
| 区间上限 upperPrice | 12.00 ¥ | 现价高于此 → 暂停定投 |
| 区间下限 lowerPrice | 8.50 ¥ | 现价低于此 → 加码买入 |
| 加码倍数 multiply | 1.5 | 加码金额 = 单期金额 × 倍数（5,000 × 1.5 = 7,500） |
| 固定止盈 takeProfitPct | 20% | 浮盈 ≥ 20% 卖出（按卖出比例） |
| 止损 stopLossPct | 10% | 浮亏 ≤ -10% 止损出清（按卖出比例） |
| 卖出比例 sellRatio | 1.0 | 触发止盈/止损时卖出的持仓比例 |
| 移动止盈 trailEnabled | 关 | 开关 |
| 移动止盈启动阈值 trailTriggerPct | 20% | 浮盈 ≥ 20% 激活 |
| 移动止盈回撤阈值 trailDrawdownPct | 8% | 自持仓最高价回撤 ≥ 8% 触发 |
| 复利再投入 reinvest | 开 | 卖出资金回收至资金池继续定投 |
| 模拟天数 simDays | 504 | ≈ 2 年交易日 |
| 年化收益 annualReturn | 8% | GBM 模拟参数 |
| 年化波动 annualVol | 22% | GBM 模拟参数 |
| 随机种子 seed | 42 | 保证模拟可复现 |
| 无风险利率 riskFreeRate | 2% | 夏普比率计算用 |

## 2. 档位判定规则

```
decideBand(price):
  price > upperPrice  → "pause"  暂停，投入 0
  price < lowerPrice  → "boost"  加码，投入 periodAmount × multiply
  否则               → "normal" 正常，投入 periodAmount
```

定投调度：`t % FREQ_STEP[frequency] === 0`（daily=1, weekly=5, monthly=21），首日 t=0 必投。

## 3. 止损 / 止盈 / 移动止盈

- **摊薄成本**：`avgCost = 累计投入 totalCost / 持仓 shares`；浮盈 `pnlPct = (price − avgCost) / avgCost`。
- **固定止盈**：`pnlPct ≥ takeProfitPct/100` → 按 sellRatio 卖出，收益按 reinvest 决定回收资金池或提取。
- **止损**：`pnlPct ≤ −(stopLossPct/100)` → 按 sellRatio 卖出。
- **移动止盈**：记录持仓期最高价 highPrice；`pnlPct ≥ trailTriggerPct/100` 时激活；激活后若 `(highPrice − price)/highPrice ≥ trailDrawdownPct/100` 触发卖出。半仓卖出后重置 highPrice 与激活态。
- **日报口径**：无真实持仓时，参考成本取现价（或昨收，需注明基准）；止损价 = 参考成本 × (1 − sl/100)，止盈价 = 参考成本 × (1 + tp/100)。

## 4. 加码区交互（关键风险点）

默认参数下：止损价 = 8.50 × 0.9 ≈ 7.65（以现价计约 8.07~8.08）**低于**加码下限 8.50。

即价格在 **[加码下限, 止损价)** 区间时（如 8.08~8.50），策略判定为"加码区"，**仍会加码买入 ¥7,500（越跌越买）**；只有跌破止损价才触发止损出清。这是"加码兜底 + 极端止损"设计，日报必须向用户提示该风险敞口，避免误以为跌破下限即止损。

## 5. 行情接口与字段格式

**本地代理**（推荐）：`GET http://localhost:8000/quote?code=sh600000`（可多代码逗号分隔）→ JSON：`{ok, name, code, price, prevClose, open, high, low, change, changePct, time}`。

**腾讯直连**：`GET https://qt.gtimg.cn/q=sh600000`，响应为 **GBK 编码**，需 `resp.read().decode("gbk", errors="replace")`。字段以 `~` 分隔（索引从 0 起）：

| 索引 | 含义 | 索引 | 含义 |
|---|---|---|---|
| 1 | 名称 | 30 | 时间戳 YYYYMMDDHHMMSS |
| 2 | 代码 | 31 | 涨跌额 |
| 3 | 现价 | 32 | 涨跌幅 % |
| 4 | 昨收 | 33 | 最高 |
| 5 | 今开 | 34 | 最低 |
| 6 | 成交量(手) | 35 | 最低（部分行情） |

解析需至少 35 个字段；浏览器直连会触发 CORS，须经本地代理。

## 6. 日报模板结构

```markdown
# 定投监控日报 · YYYY-MM-DD
> 标的 / 策略 / 数据来源 / 生成时间
## 行情快照（表格：现价/昨收/涨跌/最高/最低）
## 定投档位判定（区间 → 档位 → 本期应投金额）
## 止损/止盈点位表（参考成本基准 + 距现价 %）
## 风险提示（距止损<3% 红警；距止盈<3% 黄示；加码区交互）
## 明日操作建议
免责声明
```

## 7. 免责与合规

- 日报仅作监控参考，不执行真实交易、不构成投资建议。
- 行情数据来自腾讯免费接口，可能有延迟或波动。
