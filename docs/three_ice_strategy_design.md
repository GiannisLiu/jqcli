# 三冰策略设计文档

> `strategy/three_ice.py` v2 — 四板冰点 + 封板时间排序 + 2→3 冲击博弈

---

## 一、策略哲学

### 1.1 核心命题

A 股短线市场存在清晰的**情绪周期**。当市场最高连板高度持续下降，最终降至某一临界值时，意味着恐慌情绪达到极致——这个临界点就是"冰点"。

冰点的本质：**市场已经没有什么可以跌了**。此时物极必反，情绪向上修复的概率远大于继续恶化。在冰点次日入场，博弈情绪修复带来的高溢价，是本策略的核心逻辑。

### 1.2 为什么选 4 板作为冰点

| 连板高度 | 市场解读 |
|----------|----------|
| 7+ | 情绪极热，高标抱团 |
| 5-6 | 正常活跃期 |
| **4** | **情绪冷却，即将冰封** |
| 3 及以下 | 已经深度冰封 |

当市场最高板降到 **4 板**时，说明高标已经无法突破 5 板，但仍保留了完整的连板梯队（4→3→2→首板）。此时介入，打的是"从冷却到解冻"的第一波修复。

### 1.3 为什么不当天买、而是次日买

```
错误做法（v1）：  今天发现冰点 → 今天追买 4 板 → 明天溢价卖出
                     ^^^^^^^^^^
                     4 板已经是"被发现的"，追高性价比低

正确做法（v2）：  今天收盘确认冰点 → 明天开盘找 2 板 → 博 2→3 冲击 → 后天溢价
                                    ^^^^^^^^^^
                                    低位介入，盈亏比更好
```

冰点日的 4 板是"众矢之的"，次日可能直接分歧。而冰点日的 2 板是"暗线"，次日冲击 3 板时会产生**辨识度溢价**——市场突然发现它成了新高度。

---

## 二、完整时间线

```
Day T 15:00 (after_close)  扫描全市场涨停股 → 计算最高连板高度
                            ↓
                           最高板 == 4 ?
                            ↓ 是
                           标记 g.yesterday_was_ice = True
                            ↓
                           立即扫描当日 2 板股（连板 == 2）
                            ↓
                           对每只计算封板质量 + 板块共识
                            ↓
                           排序后缓存至 g.yesterday_candidates

Day T+1 09:35 (market_open) g.yesterday_was_ice == True ?
                            ↓ 是
                            读取 g.yesterday_candidates（收盘已缓存）
                            ↓
                            取前 5 只 → 试错买入（各占总资产 5%）
                            ↓
                            买入后：当日涨停则尾盘减半仓锁定

Day T+1 14:50 (before_close) 检查持仓：不涨停的清仓

Day T+2 09:35              持仓中：
                            - 高开 ≥ 3% → 全部止盈
                            - 涨停 → 继续持有 / 减半
                            - 不涨停 → 10:00 前清仓
                            - 持有 ≥ 2 天且不涨停 → 清仓
                            - 亏损 ≥ 5% → 止损

Day T+2 14:50              剩余持仓尾盘清仓
```

---

## 三、核心算法

### 3.1 冰点判定 (`after_close_compute`)

```
输入: 当日收盘数据
输出: g.yesterday_was_ice (bool), g.yesterday_market_height (int),
      g.yesterday_candidates (list, 冰点日缓存)

步骤:
  1. 获取全市场股票列表 (get_all_securities)
  2. 扫描前 2000 只，筛选当日涨停股 (is_limit_up)
  3. 对每只涨停股，计算连续涨停天数 (count_boards)
  4. max_board = max(所有连板天数)
  5. is_ice = (max_board == 4)
  6. 辅助: 涨停总数 < 30 → deep_ice (深度冰点)
  7. 若 is_ice: 调用 find_two_board_stocks() 扫描当日 2 板股 → 缓存候选池
  8. 存入全局变量，供次日读取
```

### 3.2 封板质量评分 (`calc_lock_quality`)

涨停时间的早晚是判断次日能否继续连板的最强信号。但我们只有日线数据（没有 tick/分钟数据），如何推断？

**用日线 OHLCV 反推封板时间：**

| 数据特征 | 推断结论 | 封板类型 | 质量分 |
|----------|----------|----------|--------|
| open≈limit, low≈limit, close≈limit, 缩量 | 开盘即涨停，全天封死 | **一字板 (gap)** | 1.0 |
| open≈limit, low<limit, close≈limit | 开盘涨停，开板后回封 | **T字板 (t_tz)** | 0.75~0.85 |
| open<limit, 缩量, low 距涨停近 | 早盘换手后封板 | **早盘板 (early)** | 0.65~0.75 |
| 放量, open 远离涨停 | 尾盘才封板 | **尾盘板 (late)** | 0.15~0.30 |
| 其他 | 常规涨停 | **常规板 (normal)** | 0.40~0.65 |

**关键公式：**

```
open_gap = (limit_price - today_open) / limit_price   // 0 = 开盘涨停
low_gap  = (limit_price - today_low)  / limit_price   // 0 = 全天未开板
vol_ratio = today_volume / prev_day_volume              // <1 = 缩量涨停
```

**判定逻辑（按优先级）：**

1. `open_gap ≤ 0.005 AND low_gap ≤ 0.005` → gap, 1.0
2. `open_gap ≤ 0.005 AND low_gap > 0.005` → t_tz, 0.85 - low_gap×2
3. `open_gap ≤ 0.02 AND vol_ratio ≤ 0.8` → early, 0.75 - open_gap×3
4. `vol_ratio ≤ 0.6 AND low_gap ≤ 0.03` → early, 0.65
5. `vol_ratio ≥ 2.5` → late, 0.25
6. `open_gap ≥ 0.05` → late, 0.15
7. 其他 → normal, 0.4 + (1 - vol_ratio/3)×0.25

尾盘板 (`quality < 0.3`) 直接过滤掉，不进入候选池。

### 3.3 候选池构建 (`find_two_board_stocks`)

由 `after_close_compute` 在冰点日收盘后直接调用，扫描**当日** 2 板股并缓存。

```
输入: context, target_date (冰点日日期)
输出: candidates[] (按封板质量排序)，存入 g.yesterday_candidates

步骤:
  1. 扫描前 2000 只股票
  2. 筛选条件:
     a. target_date 收盘涨停 (is_limit_up)
     b. target_date 连板数 == 2 (count_boards == 2)
     c. 市值 < 200 亿 (小盘更易连板)
     d. 非 ST / 非停牌
     e. 封板质量 ≥ 0.3 (过滤尾盘板)
  3. 按板块分组统计 (板块共识)
  4. 排序: lock_type(5级) DESC → sector_count DESC → lock_quality DESC
  5. 返回完整候选列表
```

### 3.4 买入分配 (`enter_positions`)

```
输入: candidates[] (已排序)
操作: 取前 min(MAX_STOCKS, available_slots) 只买入

资金分配:
  trial_cash = total_value × 0.25           // 试错总仓位 25%
  per_stock   = trial_cash / 买入数量        // 每只均分
  per_stock   = min(per_stock, cash / 买入数量)  // 不超过可用现金

最小下单: per_stock ≥ 5000 元
```

### 3.5 卖出决策 (`check_and_exit`)

| 条件 | 持有天数 | 动作 | 优先级 |
|------|----------|------|--------|
| 当日涨停 (hold_days=0) | 0 | 减半仓锁定 | 1 |
| 次日高开 ≥ 3% | 1 | 全部清仓 | 2 |
| 持有 ≥ 2 天 | 2+ | 全部清仓 | 3 |
| 亏损 ≥ 5% | 任意 | 止损清仓 | 4 |
| 次日未涨停 (尾盘) | 1+ | 尾盘清仓 | 5 |

---

## 四、参数配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ICE_BOARD_HEIGHT` | 4 | 冰点判定阈值（最高板 == 4）|
| `TARGET_BOARDS` | 2 | 次日买入的连板数（冲击 2→3）|
| `TRIAL_POSITION_PCT` | 0.25 | 冰点次日最大试错仓位占比 |
| `MAX_STOCKS` | 5 | 单日最多买入股票数 |
| `STOP_LOSS_PCT` | -0.05 | 止损线 -5% |
| `TAKE_PROFIT_HIGH_OPEN` | 0.03 | 高开止盈阈值 3% |
| `TAKE_PROFIT_LIMIT_UP` | True | 当日涨停自动减半 |
| `MAX_HOLD_DAYS` | 2 | 最大持有天数 |
| `MIN_ORDER_VALUE` | 5000 | 最小下单金额（元）|
| `FILTER_MAX_MARKET_CAP` | 200 | 市值上限（亿）|
| `FILTER_MIN_LOCK_QUALITY` | 0.3 | 最低封板质量 |
| `SCAN_STOCKS` | 2000 | 每日扫描股票数 |

---

## 五、数据流

```
                                ┌──────────────────────────────────┐
  Day T after_close             │  after_close_compute()           │
                                │                                  │
  全市场涨停股 ──────────────→ │ count_boards() 逐只计算           │
                                │ max_board = ?                    │
                                │ is_ice = (max == 4)              │
                                │                                  │
                                │ if is_ice:                       │
                                │   find_two_board_stocks()        │
                                │     ├─ scan 2000 stocks          │
                                │     ├─ filter board==2           │
                                │     ├─ calc_lock_quality         │
                                │     └─ sort by quality           │
                                │   → 缓存候选池                    │
                                └───────────┬──────────────────────┘
                                            │
                                   g.yesterday_was_ice
                                   g.yesterday_candidates
                                            │
  ════════════════════════════════════════════════════════════════════
                                            │
  Day T+1 09:35                  ┌─────────▼──────────────────────┐
                                │  market_open_trade()            │
                                │                                  │
  check_and_exit() ───────────→ │ 先处理止盈止损                   │
                                │                                  │
  g.yesterday_was_ice? ───────→ │ YES → 读取 g.yesterday_candidates│
                                │                                  │
                                │  enter_positions()               │
                                │    └─ 买前 5 只                  │
                                └──────────────────────────────────┘

  Day T+1 14:50 / Day T+2 09:35
                                ┌─────────────────────────┐
                                │  check_and_exit()       │
                                │                         │
  持仓 ───────────────────────→ │ 高开? → 清仓            │
                                │ 涨停? → 减半            │
                                │ 止损? → 清仓            │
                                │ 超期? → 清仓            │
                                │ 不板? → 清仓            │
                                └─────────────────────────┘
```

---

## 六、函数索引

| 函数 | 文件位置 | 职责 |
|------|----------|------|
| `initialize(context)` | L40 | 策略初始化，注册定时任务 |
| `after_close_compute(context)` | L127 | 收盘冰点判定 + 2 板候选池缓存 |
| `calc_lock_quality(stock, date)` | L192 | 封板质量/时间评估 |
| `find_two_board_stocks(context, target_date)` | L281 | 冰点日收盘 2 板候选筛选 |
| `enter_positions(context, candidates)` | L399 | 买入执行 |
| `check_and_exit(context)` | L448 | 卖出条件判断与执行 |
| `market_open_trade(context)` | L526 | 开盘调度入口（读取缓存买入）|
| `before_close_check(context)` | L555 | 尾盘检查入口 |
| `is_limit_up(stock, date)` | L95 | 涨停判定 |
| `count_boards(stock, end_date)` | L112 | 连板计数 |
| `_get_sector(stock)` | L382 | 行业板块获取 |

---

## 七、已知限制与后续方向

### 7.1 日线推断的局限

`calc_lock_quality` 通过 OHLCV 反推封板时间，这是一个**概率推断**而非精确数据。实际封板时间可能受到以下因素干扰：
- 尾盘炸板后回封会被误判为一字板
- 缩量可能是因为停牌而非封死

**改进方向**：如果有分钟数据或 Level-2 数据，可以直接获取精确的封板时间和封单量。

### 7.2 市值过滤的静态问题

当前 `FILTER_MAX_MARKET_CAP = 200亿` 是静态阈值，不会随市场变化调整。在极端行情下（如全面牛市），200 亿的阈值可能过滤掉过多股票。

**改进方向**：改为动态阈值，如"市值 < 全市场中位数 × 1.5"。

### 7.3 冰点深度的利用

当前 `deep_ice`（涨停总数 < 30）只用于日志标记，未参与仓位决策。深度冰点下仓位应该更激进（物极必反的确定性更高）。

**改进方向**：`deep_ice` 时提高 `TRIAL_POSITION_PCT` 到 0.35~0.40。

### 7.4 板块共识的加权

当前排序中，板块共识（同板块 2 板股数量）是第二排序键。但"同板块出现多只 3 板"本身就是一个很强的板块效应信号，可能比单一的封板质量更重要。

**改进方向**：为板块共识设置更高的权重或作为独立加分项。

---

## 八、回测记录

| 日期 | 周期 | 策略收益 | 基准收益 | Sharpe | 最大回撤 | 交易次数 |
|------|------|----------|----------|--------|----------|----------|
| 2026-06-21 | 2024-01 (22天) | +1.74% | -6.29% | 3.72 | 0.00% | 1 (100%盈) |
| — | 2024 全年 | 待跑 | — | — | — | — |

---

## 九、部署到聚宽

```bash
# 上传策略
jqcli --env-file .env --format json strategy new "三冰策略" --file strategy/three_ice.py

# 更新已有策略
jqcli --env-file .env --format json strategy edit <strategy_id> --file strategy/three_ice.py

# 发起回测
jqcli --env-file .env --format json backtest run <strategy_id> \
  --start 2024-01-02 --end 2025-12-31 \
  --capital 1000000 --freq day
```

---

> 文档生成时间: 2026-06-22 | 策略版本: v2 | 对应文件: `strategy/three_ice.py`
