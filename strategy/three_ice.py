# -*- coding: utf-8 -*-
"""
三冰策略 v2（Three Ice Strategy）
==================================

策略核心逻辑：
  收盘后计算全市场最高连板高度。当高度降至 4 板时，标记为"冰点日"。
  冰点次日开盘，寻找处于 2 连板的股票（冲击 3 板），试错买入。
  博弈逻辑：冰点后情绪修复，2 板→3 板过程产生高溢价，次日兑现。

时间线：
  Day T 收盘：计算市场高度 == 4 → 冰点确认
  Day T+1 开盘：筛选 2 板股（按最先涨停排序）→ 买入（冲击 3 连板）
  Day T+2 开盘：高开溢价 → 减半/清仓止盈

与 v1 的关键区别：
  - v1：当天发现冰点→当天买最高板（追高）
  - v2：昨天确认冰点→今天买 2 板→博弈明天 3 板溢价（低吸）
"""

from jqdata import *
import pandas as pd
import numpy as np


# ============================== 可调参数 ==============================
TRIAL_POSITION_PCT = 0.25    # 冰点次日试错仓位（占总资产比例）
MAX_STOCKS = 5               # 单日最多买入数量（选最早涨停的5只）
STOP_LOSS_PCT = -0.05        # 止损线：-5%
TAKE_PROFIT_LIMIT_UP = True  # 持仓涨停则减半锁定
TAKE_PROFIT_HIGH_OPEN = 0.03 # 高开 3% 直接清仓
MAX_HOLD_DAYS = 2            # 最大持有天数（买入次日不板就卖）
MIN_ORDER_VALUE = 5000       # 最小下单金额

# 2 板选股过滤
FILTER_MAX_MARKET_CAP = 200     # 市值上限（亿），小盘更易连板
FILTER_MIN_LOCK_QUALITY = 0.3   # 最低封板质量分（0~1），过滤尾盘板


def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)

    set_order_cost(
        OrderCost(
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            min_commission=5,
        ),
        type='stock',
    )

    # 收盘后：计算市场高度 + 判定冰点
    run_daily(after_close_compute, time='after_close', reference_security='000300.XSHG')
    # 次日开盘：冰点后买入 2 板 + 止盈昨日持仓
    run_daily(market_open_trade, time='09:35', reference_security='000300.XSHG')
    # 尾盘前：不涨停则清仓
    run_daily(before_close_check, time='14:50', reference_security='000300.XSHG')

    # ---- 全局状态 ----
    g.yesterday_was_ice = False          # 昨日是否为冰点
    g.yesterday_market_height = 0        # 昨日最高连板
    g.yesterday_candidates = []          # 冰点日收盘缓存的 2 板候选池
    g.yesterday_ice_board_stocks = []    # 昨日冰点板数的股票（供次日选股参考）

    # 持仓记录
    g.buy_date = {}       # {stock: date}
    g.buy_price = {}      # {stock: price}
    g.buy_reason = {}     # {stock: 'ice_recovery'}

    log.info('[三冰v2] 初始化完成')
    log.info(f'参数: 试错仓位={TRIAL_POSITION_PCT*100:.0f}%  最大持股={MAX_STOCKS}  '
             f'止损={STOP_LOSS_PCT*100:.0f}%  最大持有={MAX_HOLD_DAYS}天')


# ============================== 基础工具 ==============================

def get_stock_name(stock):
    try:
        info = get_security_info(stock)
        return info.display_name if info and hasattr(info, 'display_name') else ''
    except Exception:
        return ''


def format_stock(stock):
    name = get_stock_name(stock)
    return f'{stock}({name})' if name else stock


def limit_up_price(prev_close):
    return round(prev_close * 1.1, 2)


def is_limit_up(stock, date):
    """判断 stock 在 date 是否涨停收盘。"""
    try:
        hist = get_price(stock, end_date=date, count=2,
                         frequency='daily', fields=['close'],
                         skip_paused=True, fq='pre')
        if hist is None or len(hist) < 2:
            return False
        prev = hist['close'].iloc[-2]
        today = hist['close'].iloc[-1]
        if prev <= 0:
            return False
        return abs(today - limit_up_price(prev)) < 0.01
    except Exception:
        return False


def count_boards(stock, end_date):
    """计算 stock 截至 end_date 的连续涨停天数。"""
    n = 0
    d = end_date
    for _ in range(20):
        if is_limit_up(stock, d):
            n += 1
            d = d - pd.Timedelta(days=1)
        else:
            break
    return n


# ============================== 收盘后：冰点判定 + 候选筛选 ==============================

def after_close_compute(context):
    """
    收盘后：
      1. 计算当日市场最高连板高度，判断是否为四板冰点
      2. 若为冰点，立即扫描当日 2 板股 → 评分排序 → 缓存候选池
    结果存入 g，供次日开盘直接使用。
    """
    today = context.current_dt.date()
    log.info(f'========== {today} 收盘冰点判定 ==========')

    # ---- 扫描全市场最高连板 ----
    all_stocks = list(get_all_securities(['stock'], date=today).index)
    limit_up_today = []
    for s in all_stocks[:2000]:
        try:
            if is_limit_up(s, today):
                limit_up_today.append(s)
        except Exception:
            continue

    log.info(f'当日涨停股: {len(limit_up_today)}只 (扫描2000)')

    # 统计各板数分布
    board_dist = {}
    max_board = 0
    ice_board_stocks = []  # 冰点板数的股票

    for s in limit_up_today:
        try:
            b = count_boards(s, today)
            board_dist[b] = board_dist.get(b, 0) + 1
            if b > max_board:
                max_board = b
        except Exception:
            continue

    log.info(f'连板分布: {dict(sorted(board_dist.items()))}  |  最高: {max_board}板')

    # ---- 冰点判定：最高板 == 4 ----
    is_ice = (max_board == 4)

    total_limit_up = len(limit_up_today)
    deep_ice = is_ice and total_limit_up < 30

    if is_ice:
        g.ice_consecutive_days += 1
        ice_label = 'deep_ice' if deep_ice else 'ice'
        log.info(f'[ICE] 四板冰点确认！连续冰点={g.ice_consecutive_days}天  '
                 f'涨停总数={total_limit_up}  标签={ice_label}')
    else:
        if g.ice_consecutive_days > 0:
            log.info(f'冰点期结束，持续 {g.ice_consecutive_days} 天后高度恢复至 {max_board}板')
        g.ice_consecutive_days = 0

    # 存入全局变量（供次日开盘读取）
    g.yesterday_was_ice = is_ice
    g.yesterday_market_height = max_board
    g.yesterday_ice_board_stocks = ice_board_stocks
    g._yesterday_total_limit_up = total_limit_up
    g._yesterday_deep_ice = deep_ice

    # ---- 冰点日：立即扫描 2 板候选，缓存到全局变量 ----
    if is_ice:
        g.yesterday_candidates = find_two_board_stocks(context, target_date=today)
        log.info(f'冰点日候选池已缓存: {len(g.yesterday_candidates)}只 2板股')
    else:
        g.yesterday_candidates = []

    log.info(f'状态: yesterday_was_ice={is_ice}  height={max_board}板')
    log.info('##############################################################')


# ============================== 封板质量评分 ==============================

def calc_lock_quality(stock, date):
    """
    评估股票在指定日期的封板质量（近似涨停时间早晚）。

    利用日线数据推断封板时间：
      - 一字板 (open≈limit, low≈limit): 开盘即锁，质量最高
      - T字板 (open≈limit, low<limit, close≈limit): 开盘封板后短时开板
      - 早盘换手板 (open<limit, 缩量): 早盘充分换手后封板
      - 尾盘板 (open<limit, 放量, 最低价远): 尾盘才封板，质量最差

    返回 (quality_score, lock_type):
      quality_score: 0~1, 越高封板越早
      lock_type: 'gap' | 't_tz' | 'early' | 'late'
    """
    try:
        hist = get_price(stock, end_date=date, count=2,
                         frequency='daily',
                         fields=['open', 'close', 'high', 'low', 'volume'],
                         skip_paused=True, fq='pre')
        if hist is None or len(hist) < 2:
            return 0, 'no_data'

        prev = hist.iloc[-2]
        today = hist.iloc[-1]

        prev_close = prev['close']
        today_open = today['open']
        today_close = today['close']
        today_high = today['high']
        today_low = today['low']
        today_vol = today['volume']

        if prev_close <= 0:
            return 0, 'no_data'

        limit_px = limit_up_price(prev_close)

        # ---- 核心指标 ----
        # 开盘距涨停的幅度（0=开盘即涨停）
        open_gap = (limit_px - today_open) / limit_px if limit_px > 0 else 1
        # 最低价距涨停的幅度（0=全天封死未开板）
        low_gap = (limit_px - today_low) / limit_px if limit_px > 0 else 1
        # 涨停价附近振幅（越小封得越死）
        amplitude = (today_high - today_low) / limit_px if limit_px > 0 else 1
        # 相对于前一日的量比
        prev_vol = prev['volume'] if prev['volume'] > 0 else 1
        vol_ratio = today_vol / prev_vol

        # ---- 封板类型判定 ----
        if open_gap <= 0.005 and low_gap <= 0.005:
            # 一字板：全天封死，从未开板
            lock_type = 'gap'
            quality = 1.0
        elif open_gap <= 0.005 and low_gap > 0.005:
            # T字板：开盘涨停，中间开板后回封
            lock_type = 't_tz'
            # 开板幅度越小，回封越快，质量越高
            quality = 0.85 - min(low_gap * 2, 0.3)
        elif open_gap <= 0.02 and vol_ratio <= 0.8:
            # 早盘高开缩量封板
            lock_type = 'early'
            quality = 0.75 - open_gap * 3
        elif vol_ratio <= 0.6 and low_gap <= 0.03:
            # 缩量且全天未深跌 → 早盘封板
            lock_type = 'early'
            quality = 0.65
        elif vol_ratio >= 2.5:
            # 放量涨停 → 大概率尾盘板
            lock_type = 'late'
            quality = 0.25 + min(open_gap, 0.05) * 3
        elif open_gap >= 0.05:
            # 低开翻涨停 → 尾盘板
            lock_type = 'late'
            quality = 0.15 + min(open_gap, 0.08) * 2
        else:
            # 常规换手板，中性评分
            lock_type = 'normal'
            quality = 0.4 + (1 - vol_ratio / 3) * 0.25

        quality = max(0.0, min(1.0, quality))

        return quality, lock_type

    except Exception:
        return 0, 'error'


# ============================== 冰点日收盘：筛选 2 板候选 ==============================

def find_two_board_stocks(context, target_date):
    """
    在 target_date（冰点日）寻找 2 连板股票，按封板时间排序并缓存。
    由 after_close_compute 在收盘后直接调用，次日开盘直接读取缓存。

    封板越早 → 冲击 3 板概率越高 → 排前面。

    排序规则（优先级从高到低）：
      1. 一字板 (开盘即涨停，全天未开)
      2. T字板 (开盘即涨停，短暂开板回封)
      3. 早盘换手板 (高开缩量封板)
      4. 常规换手板
      5. 尾盘板 (放量/低开封板，质量最差，过滤掉)

    同一封板质量层级内，按板块共识（同板块 2 板股数量）优先。
    """
    all_stocks = list(get_all_securities(['stock'], date=target_date).index)
    candidates = []

    for s in all_stocks[:2000]:
        try:
            if not is_limit_up(s, target_date):
                continue
            if count_boards(s, target_date) != 2:
                continue

            # ---- 基础过滤 ----
            # 市值过滤
            try:
                fundamentals = get_fundamentals(
                    query(valuation.code, valuation.market_cap).filter(
                        valuation.code == s
                    ), date=target_date
                )
                if not fundamentals.empty:
                    if fundamentals['market_cap'].iloc[0] > FILTER_MAX_MARKET_CAP * 1e8:
                        continue
            except Exception:
                pass

            # 非 ST / 非停牌
            try:
                cd = get_current_data()
                if cd[s].paused or cd[s].is_st:
                    continue
            except Exception:
                continue

            # ---- 封板质量评分 ----
            lock_quality, lock_type = calc_lock_quality(s, target_date)

            if lock_quality < FILTER_MIN_LOCK_QUALITY:
                continue  # 过滤尾盘板

            sector = _get_sector(s)

            candidates.append({
                'stock': s,
                'sector': sector,
                'lock_quality': lock_quality,
                'lock_type': lock_type,
            })

        except Exception:
            continue

    if not candidates:
        return []

    # ---- 板块共识统计 ----
    sector_counts = {}
    for c in candidates:
        sec = c['sector']
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
    for c in candidates:
        c['sector_count'] = sector_counts.get(c['sector'], 1)

    # ---- 排序：封板质量第一（最先涨停），板块共识第二 ----
    lock_type_order = {'gap': 5, 't_tz': 4, 'early': 3, 'normal': 2, 'late': 1}

    candidates.sort(
        key=lambda x: (
            lock_type_order.get(x['lock_type'], 0),  # 封板类型优先级
            x['sector_count'],                         # 板块共识
            x['lock_quality'],                         # 同类型内精确分值
        ),
        reverse=True,
    )

    if candidates:
        lines = []
        for c in candidates[:12]:
            tag = c['lock_type']
            sq = '' if c['sector_count'] <= 1 else f'x{c["sector_count"]}'
            lines.append(f'{format_stock(c["stock"])}({c["sector"]}{sq}/{tag})')
        log.info(f'2板候选[{len(candidates)}只] 按封板时间排序: ' + ', '.join(lines))

    return candidates


def _get_sector(stock):
    """获取行业板块名。"""
    try:
        info = get_security_info(stock)
        if info and hasattr(info, 'industry') and info.industry:
            return info.industry
    except Exception:
        pass
    try:
        ind = get_industry(stock)
        if ind and 'sw_l1' in ind:
            return ind['sw_l1']['industry_name']
    except Exception:
        pass
    return '其他'


def enter_positions(context, candidates):
    """
    冰点次日开盘买入 2 板候选，博弈 2→3 冲击。
    按封板质量排序后的 candidates 取前 MAX_STOCKS 只。
    """
    cash = context.portfolio.available_cash
    total_value = context.portfolio.total_value
    current_holdings = list(context.portfolio.positions.keys())

    available_slots = MAX_STOCKS - len(current_holdings)
    if available_slots <= 0:
        log.info('已达最大持股数，跳过买入')
        return

    candidates = [c for c in candidates if c['stock'] not in current_holdings]
    if not candidates:
        log.info('2板候选与现有持仓重复，无新增')
        return

    trial_cash = total_value * TRIAL_POSITION_PCT
    per_stock = min(trial_cash / min(available_slots, len(candidates)),
                    cash / min(available_slots, len(candidates)))

    bought = 0
    for c in candidates:
        if bought >= available_slots:
            break
        stock = c['stock']
        if per_stock < MIN_ORDER_VALUE:
            log.warn(f'{format_stock(stock)} 分配金额 {per_stock:.0f} < 最小下单额 {MIN_ORDER_VALUE}，跳过')
            continue

        try:
            order_value(stock, per_stock)
            g.buy_date[stock] = context.current_dt.date()
            g.buy_price[stock] = get_current_data()[stock].last_price
            g.buy_reason[stock] = 'ice_2to3'

            log.info(f'[买入] {format_stock(stock)} | '
                     f'板块={c["sector"]} | 封板={c["lock_type"]} | '
                     f'板块共识={c["sector_count"]} | '
                     f'金额={per_stock:.0f} | 价格={g.buy_price[stock]:.2f}')
            bought += 1
        except Exception as e:
            log.error(f'买入 {format_stock(stock)} 失败: {e}')


# ============================== 卖出逻辑 ==============================

def check_and_exit(context):
    """检查持仓，满足条件则卖出。"""
    today = context.current_dt.date()
    to_clear = []
    to_halve = []

    for stock in list(context.portfolio.positions.keys()):
        pos = context.portfolio.positions[stock]
        if pos.total_amount <= 0:
            continue

        buy_date = g.buy_date.get(stock)
        buy_price = g.buy_price.get(stock, 0)
        current_price = get_current_data()[stock].last_price
        hold_days = (today - buy_date).days if buy_date else 999

        # ---- 买入当日：涨停则减半锁定 ----
        if hold_days == 0 and is_limit_up(stock, today) and TAKE_PROFIT_LIMIT_UP:
            if buy_price > 0:
                pnl = (current_price - buy_price) / buy_price
                to_halve.append((stock, pnl, '当日涨停减半'))
                continue

        # ---- 次日：高开 3% 清仓 ----
        if hold_days == 1 and buy_price > 0:
            open_return = (current_price - buy_price) / buy_price
            if open_return >= TAKE_PROFIT_HIGH_OPEN:
                to_clear.append((stock, f'次日高开 {open_return*100:.1f}%'))
                continue

        # ---- 持有超限 ----
        if hold_days >= MAX_HOLD_DAYS:
            to_clear.append((stock, f'超期持有{hold_days}天'))
            continue

        # ---- 止损 ----
        if buy_price > 0:
            pnl = (current_price - buy_price) / buy_price
            if pnl <= STOP_LOSS_PCT:
                to_clear.append((stock, f'止损 {pnl*100:.1f}%'))
                continue

        # ---- 次日未涨停 → 清仓（14:50 尾盘） ----
        if hold_days >= 1 and not is_limit_up(stock, today):
            to_clear.append((stock, '次日未涨停'))

    # 执行减半
    for stock, pnl, reason in to_halve:
        try:
            pos = context.portfolio.positions[stock]
            sell_val = pos.value * 0.5
            if sell_val >= MIN_ORDER_VALUE:
                order_value(stock, -sell_val)
                log.info(f'[减半] {format_stock(stock)} | {reason} | 盈亏={pnl*100:+.1f}%')
        except Exception as e:
            log.error(f'减半 {format_stock(stock)} 失败: {e}')

    # 执行清仓
    for stock, reason in to_clear:
        try:
            order_target(stock, 0)
            buy_price = g.buy_price.get(stock, 0)
            current_price = get_current_data()[stock].last_price
            pnl = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
            log.info(f'[清仓] {format_stock(stock)} | {reason} | 盈亏={pnl:+.1f}%')
            _cleanup_stock(stock)
        except Exception as e:
            log.error(f'清仓 {format_stock(stock)} 失败: {e}')


def _cleanup_stock(stock):
    g.buy_date.pop(stock, None)
    g.buy_price.pop(stock, None)
    g.buy_reason.pop(stock, None)


# ============================== 每日调度 ==============================

def market_open_trade(context):
    """
    开盘 09:35：
        - 先止盈/止损已有持仓
        - 若昨日为冰点 → 读取收盘时缓存的 2 板候选池，直接买入
    """
    today = context.current_dt.date()
    log.info(f'========== {today} 09:35 开盘交易 ==========')

    # 1. 处理已有持仓的卖出
    check_and_exit(context)

    # 2. 冰点次日 → 读取收盘时已缓存的候选池
    if g.yesterday_was_ice:
        candidates = g.yesterday_candidates
        log.info(f'昨日冰点确认(height={g.yesterday_market_height}板), '
                 f'读取收盘缓存候选池: {len(candidates)}只 2板股')

        if candidates:
            enter_positions(context, candidates)
        else:
            log.info('候选池为空，无可买入的 2 板股票')
    else:
        log.info(f'昨日非冰点(height={g.yesterday_market_height}板)，等待冰点信号')
        if g.ice_consecutive_days > 0:
            log.info(f'冰点期结束，此前连续 {g.ice_consecutive_days} 天')


def before_close_check(context):
    """
    尾盘 14:50：对不涨停的持仓做最终清仓。
    """
    log.info(f'========== {context.current_dt.date()} 14:50 尾盘检查 ==========')
    check_and_exit(context)

    # 输出持仓
    positions = context.portfolio.positions
    has_pos = False
    for stock, pos in positions.items():
        if pos.total_amount > 0:
            has_pos = True
            buy_p = g.buy_price.get(stock, 0)
            cur_p = get_current_data()[stock].last_price
            pnl = (cur_p - buy_p) / buy_p * 100 if buy_p > 0 else 0
            hold_days = (context.current_dt.date() - g.buy_date.get(stock, context.current_dt.date())).days
            log.info(f'持仓: {format_stock(stock)} | {pos.total_amount}股 | '
                     f'持有{hold_days}天 | 盈亏={pnl:+.2f}%')
    if not has_pos:
        log.info('当前空仓')
