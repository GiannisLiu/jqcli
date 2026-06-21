# -*- coding: utf-8 -*-
"""
588200 科创50ETF（易方达）长线高抛低吸策略
===========================================

策略逻辑：
  1. 首个交易日以总资产 50% 建立底仓。
  2. 此后每个交易日，将当前价与持仓均价（avg_cost）比较：
     - 价格 >= avg_cost * 1.03：卖出当前持仓市值的 10%（高抛）；
     - 价格 <= avg_cost * 0.97：用可用现金的 10% 买入（低吸）；
     - 否则不操作。
  3. 仓位硬约束：持仓市值占总资产 10% ~ 90%，超出范围不触发对应方向。
  4. 每次交易后记录日志，供回测查阅。

目标：通过震荡行情中反复高抛低吸，逐步降低持仓均成本。

参数说明：
  SECURITY        交易标的代码（聚宽格式）
  SELL_THRESHOLD  相对均价涨幅阈值，触发高抛（默认 3%）
  BUY_THRESHOLD   相对均价跌幅阈值，触发低吸（默认 3%）
  TRADE_PCT       每次买卖占持仓/现金的比例（默认 10%）
  INIT_POS_PCT    首次建仓占总资产比例（默认 50%）
  MAX_POS_PCT     持仓最大占比约束（默认 90%）
  MIN_POS_PCT     持仓最小占比约束（默认 10%）
  MIN_ORDER_VALUE 最小下单金额（元），防止碎股（默认 100）
"""

from jqdata import *

# ────────────────────────────── 可调参数 ──────────────────────────────
SECURITY       = '588200.XSHG'   # 科创50ETF 易方达
BENCHMARK      = '000300.XSHG'   # 沪深300 作业绩基准

SELL_THRESHOLD = 1.03   # 当价 >= avg_cost * 1.03 触发高抛
BUY_THRESHOLD  = 0.97   # 当价 <= avg_cost * 0.97 触发低吸
TRADE_PCT      = 0.10   # 每次操作占持仓市值/可用现金的比例

INIT_POS_PCT   = 0.50   # 首日建仓占总资产的比例
MAX_POS_PCT    = 0.90   # 持仓市值/总资产 上限
MIN_POS_PCT    = 0.10   # 持仓市值/总资产 下限
MIN_ORDER_VALUE = 100   # 最小有效下单金额（元）


# ────────────────────────────── 初始化 ──────────────────────────────
def initialize(context):
    """
    策略初始化：设定基准、开启真实价格、设定 ETF 佣金。
    """
    set_benchmark(BENCHMARK)
    set_option('use_real_price', True)

    # ETF 印花税为 0，双边佣金各 0.03%，最低 5 元
    set_order_cost(
        OrderCost(
            open_commission=0.0003,
            close_commission=0.0003,
            min_commission=5,
        ),
        type='fund',
    )

    g.initialized = False   # 是否已完成首次建仓
    log.info('initialize done | security=%s benchmark=%s', SECURITY, BENCHMARK)


# ────────────────────────────── 每日交易 ──────────────────────────────
def handle_data(context, data):
    """
    每个交易日触发一次，依据价格相对持仓均价的偏离执行高抛低吸。
    """
    sec = SECURITY
    current = get_current_data()

    # 获取当前价格
    price = current[sec].last_price
    if not price or price <= 0:
        log.warn('handle_data: 无效价格 price=%s，跳过', price)
        return

    portfolio  = context.portfolio
    total      = portfolio.total_value          # 总资产（持仓市值 + 现金）
    cash       = portfolio.available_cash       # 可用现金
    pos        = portfolio.positions.get(sec)
    pos_value  = pos.value          if pos else 0.0
    avg_cost   = pos.avg_cost       if (pos and pos.total_amount > 0) else 0.0
    pos_ratio  = pos_value / total  if total > 0 else 0.0

    # ── 1. 首次建仓 ───────────────────────────────────────────────────
    if not g.initialized:
        target = total * INIT_POS_PCT
        if target >= MIN_ORDER_VALUE:
            order_value(sec, target)
            log.info(
                '【建仓】买入 %.2f 元 | price=%.3f | 目标仓位=%.0f%%',
                target, price, INIT_POS_PCT * 100,
            )
        g.initialized = True
        return

    # ── 无持仓保护：均价为 0 则跳过高抛低吸判断 ─────────────────────
    if avg_cost <= 0:
        return

    # ── 2. 高抛：价格涨超阈值且仓位高于下限 ─────────────────────────
    if price >= avg_cost * SELL_THRESHOLD and pos_ratio > MIN_POS_PCT:
        sell_value = pos_value * TRADE_PCT
        if sell_value >= MIN_ORDER_VALUE:
            order_value(sec, -sell_value)
            log.info(
                '【高抛】卖出 %.2f 元 | price=%.3f avg=%.3f'
                ' 涨幅=%.2f%% pos=%.1f%%',
                sell_value, price, avg_cost,
                (price / avg_cost - 1) * 100, pos_ratio * 100,
            )
        return

    # ── 3. 低吸：价格跌破阈值且仓位低于上限 ─────────────────────────
    if price <= avg_cost * BUY_THRESHOLD and pos_ratio < MAX_POS_PCT:
        buy_value = cash * TRADE_PCT
        if buy_value >= MIN_ORDER_VALUE:
            order_value(sec, buy_value)
            log.info(
                '【低吸】买入 %.2f 元 | price=%.3f avg=%.3f'
                ' 跌幅=%.2f%% pos=%.1f%%',
                buy_value, price, avg_cost,
                (1 - price / avg_cost) * 100, pos_ratio * 100,
            )


# ────────────────────────────── 收盘后日志 ──────────────────────────────
def after_trading_end(context):
    """
    每日收盘后输出持仓快照，方便回测日志分析。
    """
    pos   = context.portfolio.positions.get(SECURITY)
    total = context.portfolio.total_value
    cash  = context.portfolio.available_cash

    if pos and pos.total_amount > 0:
        pos_ratio = pos.value / total if total > 0 else 0.0
        log.info(
            '【收盘】持股=%d 均价=%.3f 市值=%.2f 仓位=%.1f%% 现金=%.2f 总资产=%.2f',
            pos.total_amount, pos.avg_cost, pos.value,
            pos_ratio * 100, cash, total,
        )
    else:
        log.info('【收盘】空仓 现金=%.2f 总资产=%.2f', cash, total)
