# 导入函数库
from jqdata import *
import pandas as pd
import numpy as np

# 初始化函数，设定基准等等
def initialize(context):
    # 设定沪深300作为基准
    set_benchmark('000300.XSHG')
    # 开启动态复权模式(真实价格)
    set_option('use_real_price', True)
    # 输出内容到日志 log.info()
    log.info('初始函数开始运行且全局只运行一次')
    # 过滤掉order系列API产生的比error级别低的log
    # log.set_level('order', 'error')

    ### 股票相关设定 ###
    # 股票类每笔交易时的手续费是：买入时佣金万分之三，卖出时佣金万分之三加千分之一印花税, 每笔交易佣金最低扣5块钱
    set_order_cost(OrderCost(close_tax=0.001, open_commission=0.0003, close_commission=0.0003, min_commission=5), type='stock')

    ## 运行函数（reference_security为运行时间的参考标的；传入的标的只做种类区分，因此传入'000300.XSHG'或'510300.XSHG'是一样的）
      # 开盘前运行
    run_daily(before_market_open, time='before_open', reference_security='000300.XSHG')
      # 竞价结束后运行（9:25）
    run_daily(after_auction, time='09:25', reference_security='000300.XSHG')
      # 开盘时运行
    run_daily(market_open, time='open', reference_security='000300.XSHG')
      # 收盘后运行
    run_daily(after_market_close, time='after_close', reference_security='000300.XSHG')

    # 全局变量
    g.stocks = []  # 候选股票列表
    g.hold_stocks = []  # 当前持有的股票列表
    g.buy_prices = {}  # 买入价格字典
    g.buy_dates = {}  # 买入日期字典
    g.max_hold_stocks = 3  # 最大持有股票数量
    g.market_status = 'neutral'  # 市场状态：bull, bear, neutral
    g.emotion_cycle = 'downtrend'  # 情绪周期：uptrend, downtrend, consolidation
    g.rabbit_stocks = []  # 一字板股票列表（兔子）
    g.turtle_stocks = []  # 换手板股票列表（乌龟）
    g.theme_analysis = {}  # 题材分析结果
    g.current_theme = ''  # 当前主流题材

## 辅助函数：获取股票名称
def get_stock_name(stock_code):
    """获取股票名称"""
    try:
        security_info = get_security_info(stock_code)
        if security_info and hasattr(security_info, 'display_name'):
            return security_info.display_name
        return ''
    except:
        return ''

## 辅助函数：格式化股票信息（代码+名称）
def format_stock_info(stock_code):
    """格式化股票信息，返回'代码(名称)'格式"""
    name = get_stock_name(stock_code)
    if name:
        return f"{stock_code}({name})"
    return stock_code

## 辅助函数：判断是否为竞价涨停的股票
def is_auction_limit_up(stock_code, context):
    """判断股票是否为竞价涨停（在竞价结束后使用当天开盘价判断）"""
    try:
        current_date = context.current_dt.date()
        
        # 获取前一天的收盘价
        prev_date = current_date - pd.Timedelta(days=1)
        hist_data = get_price(stock_code, end_date=prev_date, count=1, frequency='daily', fields=['close'])
        if hist_data.empty:
            return False
        
        prev_close = hist_data['close'][-1]
        
        # 计算涨停价格
        limit_up_price = prev_close * 1.1
        limit_up_price = round(limit_up_price, 2)  # 四舍五入到2位小数
        
        # 获取当天的开盘价（竞价结束后开盘价已确定）
        # 尝试多种方式获取开盘价
        current_data = get_current_data()[stock_code]
        today_open = 0
        
        # 方式1：尝试获取open_price属性
        if hasattr(current_data, 'open_price') and current_data.open_price > 0:
            today_open = current_data.open_price
        # 方式2：尝试获取morning_open属性
        elif hasattr(current_data, 'morning_open') and current_data.morning_open > 0:
            today_open = current_data.morning_open
        # 方式3：尝试获取last_price属性（可能是竞价价格）
        elif hasattr(current_data, 'last_price') and current_data.last_price > 0:
            today_open = current_data.last_price
        # 方式4：尝试获取high_price属性
        elif hasattr(current_data, 'high_price') and current_data.high_price > 0:
            today_open = current_data.high_price
        # 方式5：尝试获取low_price属性
        elif hasattr(current_data, 'low_price') and current_data.low_price > 0:
            today_open = current_data.low_price
        else:
            # 如果都获取不到，记录日志并返回False
            log.info(f"无法获取 {stock_code} 的开盘价")
            return False
        
        # 判断是否开盘接近涨停（允许微小误差）
        if abs(today_open - limit_up_price) < 0.02:
            log.info(f"发现一字板: {stock_code}, 开盘价: {today_open}, 涨停价: {limit_up_price}")
            return True
        return False
    except Exception as e:
        log.info(f"判断竞价涨停失败: {e}")
        return False

## 辅助函数：分析一字板封单量
def analyze_limit_up_volume(stock_code, context):
    """分析一字板股票的封单量（在开盘后使用当天开盘价判断）"""
    try:
        current_date = context.current_dt.date()
        
        # 获取前一天的收盘价
        prev_date = current_date - pd.Timedelta(days=1)
        hist_data = get_price(stock_code, end_date=prev_date, count=1, frequency='daily', fields=['close'])
        if hist_data.empty:
            return 0
        
        prev_close = hist_data['close'][-1]
        
        # 计算涨停价格
        limit_up_price = prev_close * 1.1
        limit_up_price = round(limit_up_price, 2)
        
        # 获取当天的开盘价（开盘后开盘价已确定）
        # 尝试多种方式获取开盘价（与is_auction_limit_up函数保持一致）
        current_data = get_current_data()[stock_code]
        today_open = 0
        
        # 方式1：尝试获取open_price属性
        if hasattr(current_data, 'open_price') and current_data.open_price > 0:
            today_open = current_data.open_price
        # 方式2：尝试获取morning_open属性
        elif hasattr(current_data, 'morning_open') and current_data.morning_open > 0:
            today_open = current_data.morning_open
        # 方式3：尝试获取last_price属性（可能是竞价价格）
        elif hasattr(current_data, 'last_price') and current_data.last_price > 0:
            today_open = current_data.last_price
        # 方式4：尝试获取high_price属性
        elif hasattr(current_data, 'high_price') and current_data.high_price > 0:
            today_open = current_data.high_price
        # 方式5：尝试获取low_price属性
        elif hasattr(current_data, 'low_price') and current_data.low_price > 0:
            today_open = current_data.low_price
        else:
            # 如果都获取不到，返回0
            return 0
        
        # 判断是否为一字板
        if abs(today_open - limit_up_price) >= 0.02:
            return 0
        
        # 获取当天的成交量（开盘后成交量可能还很小）
        # 这里返回一个固定值表示是一字板
        return 1.0  # 返回1亿，表示是一字板
    except Exception as e:
        log.info(f"分析一字板封单量失败: {e}")
        return 0

## 辅助函数：识别兔子股票（一字板）
def identify_rabbit_stocks(context):
    """识别兔子股票（一字板，在竞价结束后识别）"""
    try:
        rabbit_stocks = []
        current_date = context.current_dt.date()
        all_stocks = get_all_securities(['stock'], date=current_date).index.tolist()
        
        log.info(f"开始识别兔子股票（一字板），当前时间: {context.current_dt}")
        
        # 筛选一字板股票
        for stock in all_stocks[:1500]:  # 检查前1500只股票
            try:
                # 判断是否为竞价涨停（使用当天开盘价）
                if is_auction_limit_up(stock, context):
                    # 分析封单量（返回成交额，单位：亿元）
                    volume_ratio = analyze_limit_up_volume(stock, context)
                    stock_info = format_stock_info(stock)
                    log.info(f"一字板 {stock_info} 封单量: {volume_ratio:.2f}亿")
                    if volume_ratio > 0.5:  # 成交额大于0.5亿元
                        rabbit_stocks.append((stock, volume_ratio))
                        log.info(f"发现兔子股票: {stock_info}, 封单成交额: {volume_ratio:.2f}亿")
            except Exception as e:
                log.info(f"检查兔子股票 {stock} 失败: {e}")
        
        # 按封单量排序
        rabbit_stocks.sort(key=lambda x: x[1], reverse=True)
        # 选择前5只
        rabbit_stocks = rabbit_stocks[:5]
        
        return [stock for stock, _ in rabbit_stocks]
    except Exception as e:
        log.info(f"识别兔子股票失败: {e}")
        return []

## 辅助函数：获取股票所属行业
def get_stock_industry(stock_code):
    """获取股票所属行业"""
    try:
        # 使用get_security_info获取股票信息
        security_info = get_security_info(stock_code)
        if security_info:
            # 尝试获取行业信息
            if hasattr(security_info, 'industry'):
                return security_info.industry
            # 尝试获取行业代码
            if hasattr(security_info, 'industry_code'):
                return security_info.industry_code
        return None
    except Exception as e:
        log.info(f"获取股票行业失败: {e}")
        return None

## 辅助函数：获取行业内的所有股票
def get_industry_stocks_list(industry_name, date):
    """获取某个行业内的所有股票"""
    try:
        # 使用get_industry_stocks获取行业股票
        stocks = get_industry_stocks(industry_name, date=date)
        return stocks
    except Exception as e:
        log.info(f"获取行业股票失败: {e}")
        return []

## 辅助函数：获取股票所属概念板块
def get_stock_concepts(stock_code, date):
    """获取股票所属的概念板块"""
    try:
        # 注意：get_concepts()函数不接受参数，返回所有概念
        # 我们需要使用其他方式获取股票所属概念
        # 暂时返回空列表，后续可以优化
        return []
    except Exception as e:
        log.info(f"获取股票概念失败: {e}")
        return []

## 辅助函数：获取概念板块内的所有股票
def get_concept_stocks_list(concept_code, date):
    """获取某个概念板块内的所有股票"""
    try:
        # 注意：get_concept_stocks()函数可能不接受参数或参数不同
        # 暂时返回空列表，后续可以优化
        return []
    except Exception as e:
        log.info(f"获取概念股票失败: {e}")
        return []

## 辅助函数：获取板块内股票
def get_industry_peers(stock_code, date):
    """获取股票所属行业的其他股票"""
    try:
        industry = get_stock_industry(stock_code)
        if industry:
            stocks = get_industry_stocks_list(industry, date)
            # 排除自身
            return [s for s in stocks if s != stock_code]
        return []
    except Exception as e:
        log.info(f"获取行业股票失败: {e}")
        return []

## 辅助函数：获取概念板块股票
def get_concept_peers(stock_code, date):
    """获取股票所属概念板块的其他股票"""
    try:
        concepts = get_stock_concepts(stock_code, date)
        if concepts:
            all_stocks = set()
            for concept in concepts:
                stocks = get_concept_stocks_list(concept, date)
                all_stocks.update(stocks)
            # 排除自身
            return [s for s in all_stocks if s != stock_code]
        return []
    except Exception as e:
        log.info(f"获取概念股票失败: {e}")
        return []

## 辅助函数：判断是否为换手板（回测版本）
def is_handover_stock(stock_code, current_date):
    """判断股票是否为换手板（使用前一天数据，防止未来函数）"""
    try:
        # 使用前一天的数据（end_date设置为前一天，防止未来函数）
        prev_date = current_date - pd.Timedelta(days=1)
        hist_data = get_price(stock_code, end_date=prev_date, count=2, frequency='daily', fields=['open', 'close', 'high', 'low', 'volume', 'money'])
        if hist_data.empty or len(hist_data) < 2:
            return False
        
        # 换手板条件：涨停且有一定成交量
        prev_close = hist_data['close'][-2]
        today_close = hist_data['close'][-1]
        today_volume = hist_data['volume'][-1]
        today_money = hist_data['money'][-1] if 'money' in hist_data.columns else 0
        
        # 计算涨停价格
        limit_up_price = prev_close * 1.1
        limit_up_price = round(limit_up_price, 2)
        
        # 判断是否涨停
        if abs(today_close - limit_up_price) >= 0.01:
            return False
        
        # 使用成交额作为换手率的参考指标
        # 成交额大于1亿元视为换手板
        if today_money > 100000000:
            return True
        return False
    except Exception as e:
        log.info(f"判断换手板失败: {e}")
        return False

## 辅助函数：识别乌龟股票（同板块同身位换手板）
def identify_turtle_stocks(context, rabbit_stocks):
    """识别乌龟股票（同板块同身位换手板）"""
    try:
        turtle_stocks = []
        current_date = context.current_dt.date()
        prev_date = current_date - pd.Timedelta(days=1)  # 使用前一天的数据，防止未来函数
        
        # 格式化兔子股票列表（带名称）
        rabbit_list_with_names = [format_stock_info(r) for r in rabbit_stocks]
        log.info(f"开始寻找乌龟股票，兔子股票列表: {rabbit_list_with_names}")
        
        # 步骤1：获取兔子股票所属的行业和概念板块
        rabbit_industries = {}
        rabbit_concepts = {}
        
        for rabbit in rabbit_stocks:
            # 获取行业
            industry = get_stock_industry(rabbit)
            if industry:
                rabbit_industries[rabbit] = industry
                log.info(f"兔子股票 {format_stock_info(rabbit)} 所属行业: {industry}")
            
            # 获取概念板块
            concepts = get_stock_concepts(rabbit, prev_date)
            if concepts:
                rabbit_concepts[rabbit] = concepts
                log.info(f"兔子股票 {format_stock_info(rabbit)} 所属概念: {concepts[:3]}...")  # 只显示前3个
        
        # 步骤2：获取同板块的候选股票池
        candidate_stocks = set()
        
        # 从行业获取
        for rabbit, industry in rabbit_industries.items():
            industry_stocks = get_industry_stocks_list(industry, prev_date)
            candidate_stocks.update(industry_stocks)
            log.info(f"从行业 {industry} 获取到 {len(industry_stocks)} 只股票")
        
        # 从概念板块获取
        for rabbit, concepts in rabbit_concepts.items():
            for concept in concepts:
                concept_stocks = get_concept_stocks_list(concept, prev_date)
                candidate_stocks.update(concept_stocks)
        
        # 排除兔子股票
        candidate_stocks = candidate_stocks - set(rabbit_stocks)
        log.info(f"同板块候选股票池大小: {len(candidate_stocks)}")
        
        # 步骤3：从候选股票池中筛选换手板股票
        for stock in candidate_stocks:
            try:
                # 判断是否为换手板
                if is_handover_stock(stock, current_date):
                    # 获取成交额（使用前一天的数据，防止未来函数）
                    hist_data = get_price(stock, end_date=prev_date, count=2, frequency='daily', fields=['close', 'money'])
                    if not hist_data.empty and len(hist_data) >= 2:
                        today_money = hist_data['money'][-1] if 'money' in hist_data.columns else 0
                        
                        # 找出与该乌龟股票同板块的兔子股票
                        matched_rabbits = []
                        stock_industry = get_stock_industry(stock)
                        stock_concepts = get_stock_concepts(stock, prev_date)
                        
                        for rabbit in rabbit_stocks:
                            # 检查是否同行业
                            if stock_industry and rabbit_industries.get(rabbit) == stock_industry:
                                matched_rabbits.append(rabbit)
                            # 检查是否有共同概念
                            elif stock_concepts and rabbit_concepts.get(rabbit):
                                common_concepts = set(stock_concepts) & set(rabbit_concepts[rabbit])
                                if common_concepts:
                                    matched_rabbits.append(rabbit)
                        
                        if matched_rabbits:
                            turtle_stocks.append((stock, today_money, matched_rabbits))
                            stock_info = format_stock_info(stock)
                            matched_rabbit_info = [format_stock_info(r) for r in matched_rabbits]
                            log.info(f"发现换手板股票: {stock_info}, 成交额: {today_money/100000000:.2f}亿, 匹配兔子: {matched_rabbit_info}")
            except Exception as e:
                log.info(f"检查乌龟股票 {stock} 失败: {e}")
        
        # 步骤4：如果同板块没有找到换手板，则从所有股票中寻找（降级策略）
        if not turtle_stocks:
            log.info("同板块未找到换手板股票，启动降级策略：从所有股票中寻找")
            all_stocks = get_all_securities(['stock'], date=current_date).index.tolist()
            
            for stock in all_stocks[:2000]:  # 检查前2000只股票
                try:
                    if stock in rabbit_stocks:
                        continue
                    
                    if is_handover_stock(stock, current_date):
                        hist_data = get_price(stock, end_date=prev_date, count=2, frequency='daily', fields=['close', 'money'])
                        if not hist_data.empty and len(hist_data) >= 2:
                            today_money = hist_data['money'][-1] if 'money' in hist_data.columns else 0
                            turtle_stocks.append((stock, today_money, rabbit_stocks))
                            stock_info = format_stock_info(stock)
                            log.info(f"发现换手板股票(降级): {stock_info}, 成交额: {today_money/100000000:.2f}亿")
                except Exception as e:
                    log.info(f"检查乌龟股票 {stock} 失败: {e}")
        
        # 按成交额排序
        turtle_stocks.sort(key=lambda x: x[1], reverse=True)
        # 选择前5只
        turtle_stocks = turtle_stocks[:5]
        
        result = [stock for stock, _, _ in turtle_stocks]
        result_with_names = [format_stock_info(s) for s in result]
        log.info(f"识别到 {len(result)} 只乌龟股票: {result_with_names}")
        
        # 详细记录每只乌龟股票对应的兔子股票（带名称）
        for turtle, money, matched_rabbits in turtle_stocks:
            turtle_info = format_stock_info(turtle)
            rabbit_names = [format_stock_info(r) for r in matched_rabbits]
            log.info(f"乌龟股票 {turtle_info} (成交额{money/100000000:.2f}亿) -> 匹配兔子股票: {rabbit_names}")
        
        return result
    except Exception as e:
        log.info(f"识别乌龟股票失败: {e}")
        return []

## 辅助函数：判断是否为强势股
def is_strong_stock(stock_code, current_date):
    """判断是否为强势股"""
    try:
        # 获取近期数据
        hist_data = get_price(stock_code, end_date=current_date, count=5, frequency='daily', fields=['close', 'volume'])
        if hist_data.empty or len(hist_data) < 5:
            return False, 0
        
        # 计算5日涨幅
        return_5d = (hist_data['close'][-1] / hist_data['close'][0] - 1) * 100
        # 计算成交量
        avg_volume = hist_data['volume'].mean()
        
        # 筛选条件：5日涨幅大于8%，且成交量放大
        if return_5d > 8 and avg_volume > 800000:
            return True, return_5d
        return False, return_5d
    except Exception as e:
        log.info(f"判断强势股失败: {e}")
        return False, 0

## 辅助函数：分析市场状态
def analyze_market_status(context):
    """分析当前市场状态"""
    try:
        # 获取沪深300指数数据
        index_data = get_price('000300.XSHG', end_date=context.current_dt, count=5, frequency='daily', fields=['close'])
        if index_data.empty or len(index_data) < 5:
            return 'neutral'
        
        # 计算5日涨幅（缩短周期以提高灵敏度）
        index_return = (index_data['close'][-1] / index_data['close'][0] - 1) * 100
        log.info(f"沪深300 5日涨幅: {index_return:.2f}%")
        
        if index_return > 3:
            return 'bull'
        elif index_return < -2:
            return 'bear'
        else:
            return 'neutral'
    except Exception as e:
        log.info(f"分析市场状态失败: {e}")
        return 'neutral'

## 辅助函数：分析情绪周期
def analyze_emotion_cycle(context):
    """分析当前情绪周期（使用前一天数据，防止未来函数）"""
    try:
        # 获取涨停股票数量
        current_date = context.current_dt.date()
        prev_date = current_date - pd.Timedelta(days=1)  # 使用前一天的数据，防止未来函数
        all_stocks = get_all_securities(['stock'], date=prev_date).index.tolist()
        
        limit_up_count = 0
        # 检查更多股票以准确统计涨停数量（之前只检查1000只导致统计不准确）
        for stock in all_stocks[:3000]:  # 检查前3000只股票
            try:
                hist_data = get_price(stock, end_date=prev_date, count=2, frequency='daily', fields=['close'])
                if not hist_data.empty and len(hist_data) >= 2:
                    prev_close = hist_data['close'][-2]
                    today_close = hist_data['close'][-1]
                    limit_up_price = prev_close * 1.1
                    limit_up_price = round(limit_up_price, 2)
                    if abs(today_close - limit_up_price) < 0.02:
                        limit_up_count += 1
            except:
                pass
        
        # 获取沪深两市的成交额
        try:
            # 注意：指数的成交额不能代表整个市场的成交额
            # 这里使用沪深300指数的成交额作为市场活跃度的参考
            # 实际的市场总成交额需要通过其他方式获取
            
            # 获取沪深300指数成交额（使用前一天的数据，防止未来函数）
            index_data = get_price('000300.XSHG', end_date=prev_date, count=5, frequency='daily', fields=['money'])
            if not index_data.empty:
                avg_volume = index_data['money'].mean()
                log.info(f"沪深300成交额: {avg_volume/100000000:.2f}亿")
            else:
                avg_volume = 0
                log.info("无法获取沪深300成交额")
                
            # 对于情绪周期判断，我们使用涨停股票数量作为主要指标
            # 成交额作为辅助参考
        except Exception as e:
            log.info(f"获取成交额失败: {e}")
            avg_volume = 0
        
        log.info(f"涨停股票数: {limit_up_count}, 平均成交额: {avg_volume/100000000:.2f}亿")
        
        # 判断情绪周期
        # 根据实际市场数据调整阈值
        if limit_up_count > 30 and avg_volume > 150000000000:
            return 'uptrend'  # 情绪主升
        elif limit_up_count < 10 and avg_volume < 80000000000:
            return 'downtrend'  # 情绪下降
        else:
            return 'consolidation'  # 情绪震荡
    except Exception as e:
        log.info(f"分析情绪周期失败: {e}")
        return 'consolidation'

## 辅助函数：分析题材热度
def analyze_theme_heat(context):
    """分析当前市场题材热度"""
    try:
        current_date = context.current_dt.date()
        all_stocks = get_all_securities(['stock'], date=current_date).index.tolist()
        
        # 简单的题材分析逻辑
        # 实际应用中可能需要更复杂的题材识别算法
        theme_stocks = {
            'AI': ['002230.XSHE', '300418.XSHE', '002410.XSHE'],  # AI相关股票
            '新能源': ['600549.XSHG', '002594.XSHE', '300750.XSHE'],  # 新能源相关股票
            '医药': ['600276.XSHG', '000661.XSHE', '300003.XSHE'],  # 医药相关股票
            '半导体': ['600703.XSHG', '002049.XSHE', '300661.XSHE'],  # 半导体相关股票
            '券商': ['600030.XSHG', '601688.XSHG', '000776.XSHE']  # 券商相关股票
        }
        
        theme_heat = {}
        for theme, stocks in theme_stocks.items():
            limit_up_count = 0
            for stock in stocks:
                if stock in all_stocks:
                    try:
                        hist_data = get_price(stock, end_date=current_date, count=2, frequency='daily', fields=['close'])
                        if not hist_data.empty and len(hist_data) >= 2:
                            prev_close = hist_data['close'][-2]
                            today_close = hist_data['close'][-1]
                            limit_up_price = prev_close * 1.1
                            limit_up_price = round(limit_up_price, 2)
                            if abs(today_close - limit_up_price) < 0.02:
                                limit_up_count += 1
                    except:
                        pass
            theme_heat[theme] = limit_up_count
        
        # 找出最热的题材
        if theme_heat:
            hottest_theme = max(theme_heat, key=theme_heat.get)
            if theme_heat[hottest_theme] >= 2:  # 至少有2只股票涨停
                log.info(f"当前最热题材: {hottest_theme}, 涨停数: {theme_heat[hottest_theme]}")
                return hottest_theme, theme_heat
        
        return '', theme_heat
    except Exception as e:
        log.info(f"分析题材热度失败: {e}")
        return '', {}

## 辅助函数：筛选候选股票
def select_candidate_stocks(context):
    """使用龟兔模式筛选候选股票"""
    candidates = []
    current_date = context.current_dt.date()
    
    # 分析市场状态
    g.market_status = analyze_market_status(context)
    log.info(f"当前市场状态: {g.market_status}")
    
    # 分析情绪周期
    g.emotion_cycle = analyze_emotion_cycle(context)
    log.info(f"当前情绪周期: {g.emotion_cycle}")
    
    # 分析题材热度
    g.current_theme, g.theme_analysis = analyze_theme_heat(context)
    log.info(f"当前主流题材: {g.current_theme}")
    
    # 只有在情绪主升期才使用龟兔模式
    if g.emotion_cycle != 'uptrend':
        log.info('情绪周期不是主升期，暂不使用龟兔模式')
        return []
    
    # 1. 识别兔子股票（一字板）
    log.info('开始识别兔子股票（一字板）')
    g.rabbit_stocks = identify_rabbit_stocks(context)
    rabbit_names = [format_stock_info(r) for r in g.rabbit_stocks]
    log.info(f"识别到 {len(g.rabbit_stocks)} 只兔子股票: {rabbit_names}")
    
    # 如果没有兔子股票，不进行后续操作
    if not g.rabbit_stocks:
        log.info('未识别到兔子股票，暂不使用龟兔模式')
        return []
    
    # 2. 识别乌龟股票（同板块同身位换手板）
    log.info('开始识别乌龟股票（同板块同身位换手板）')
    g.turtle_stocks = identify_turtle_stocks(context, g.rabbit_stocks)
    turtle_names = [format_stock_info(t) for t in g.turtle_stocks]
    log.info(f"识别到 {len(g.turtle_stocks)} 只乌龟股票: {turtle_names}")
    
    # 3. 生成候选股票列表
    # 优先选择乌龟股票
    for turtle in g.turtle_stocks:
        if turtle not in candidates:
            candidates.append(turtle)
            turtle_info = format_stock_info(turtle)
            log.info(f'添加乌龟股票作为候选：{turtle_info}')
    
    # 限制候选股票数量
    max_candidates = 3  # 最多3只候选股票
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]
    
    candidates_with_names = [format_stock_info(c) for c in candidates]
    log.info(f'最终候选股票列表：{candidates_with_names}')
    return candidates

## 开盘前运行函数
def before_market_open(context):
    # 输出运行时间
    log.info('函数运行时间(before_market_open)：'+str(context.current_dt.time()))

    # 给微信发送消息（添加模拟交易，并绑定微信生效）
    # send_message('美好的一天~')
    
    # 分析市场状态（使用前一天数据）
    g.market_status = analyze_market_status(context)
    log.info(f"当前市场状态: {g.market_status}")
    
    # 分析情绪周期（使用前一天数据）
    g.emotion_cycle = analyze_emotion_cycle(context)
    log.info(f"当前情绪周期: {g.emotion_cycle}")
    
    # 分析题材热度（使用前一天数据）
    g.current_theme, g.theme_analysis = analyze_theme_heat(context)
    log.info(f"当前主流题材: {g.current_theme}")

## 竞价结束后运行函数
def after_auction(context):
    """竞价结束后运行（9:25），暂不进行股票筛选"""
    log.info('函数运行时间(after_auction)：'+str(context.current_dt.time()))
    log.info('竞价结束，等待开盘后筛选股票')

## 辅助函数：计算风险指数
def calculate_risk_index(context):
    """计算当前市场风险指数（使用前一天数据，防止未来函数）"""
    try:
        # 基于多个因子计算风险指数
        current_date = context.current_dt.date()
        prev_date = current_date - pd.Timedelta(days=1)  # 使用前一天的数据，防止未来函数
        
        # 1. 市场波动率
        index_data = get_price('000300.XSHG', end_date=prev_date, count=10, frequency='daily', fields=['close', 'money'])
        if index_data.empty:
            return 50  # 默认风险指数
        
        # 计算标准差
        returns = index_data['close'].pct_change().dropna()
        volatility = returns.std() * 100
        
        # 2. 涨停股票数量
        all_stocks = get_all_securities(['stock'], date=prev_date).index.tolist()
        limit_up_count = 0
        for stock in all_stocks[:2000]:  # 检查前2000只股票（与情绪周期判断保持一致）
            try:
                hist_data = get_price(stock, end_date=prev_date, count=2, frequency='daily', fields=['close'])
                if not hist_data.empty and len(hist_data) >= 2:
                    prev_close = hist_data['close'][-2]
                    today_close = hist_data['close'][-1]
                    limit_up_price = prev_close * 1.1
                    limit_up_price = round(limit_up_price, 2)
                    if abs(today_close - limit_up_price) < 0.02:
                        limit_up_count += 1
            except:
                pass
        
        # 3. 市场成交额
        avg_volume = index_data['money'].mean() if 'money' in index_data.columns else 0
        
        # 综合计算风险指数（0-100，越高风险越大）
        risk_index = 50
        
        # 波动率因子
        if volatility > 2:
            risk_index += 20
        elif volatility < 0.5:
            risk_index -= 10
        
        # 涨停数量因子
        if limit_up_count > 100:
            risk_index += 15  # 涨停过多，市场可能过热
        elif limit_up_count < 20:
            risk_index -= 5
        
        # 成交额因子
        if avg_volume > 400000000000:
            risk_index += 10  # 成交额过大，市场可能过热
        elif avg_volume < 100000000000:
            risk_index -= 5
        
        # 情绪周期因子
        if g.emotion_cycle == 'uptrend':
            risk_index += 15  # 情绪主升期，风险积累
        elif g.emotion_cycle == 'downtrend':
            risk_index -= 10
        
        # 限制在0-100之间
        risk_index = max(0, min(100, risk_index))
        log.info(f"当前市场风险指数: {risk_index}")
        
        return risk_index
    except Exception as e:
        log.info(f"计算风险指数失败: {e}")
        return 50

## 开盘时运行函数
def market_open(context):
    log.info('函数运行时间(market_open):'+str(context.current_dt.time()))
    
    # 先进行股票筛选（开盘后可以获取当天的开盘价）
    # 只有在情绪主升期才使用龟兔模式
    if g.emotion_cycle == 'uptrend':
        # 识别兔子股票（一字板，使用当天开盘价判断）
        log.info('开始识别兔子股票（一字板）')
        g.rabbit_stocks = identify_rabbit_stocks(context)
        rabbit_names = [format_stock_info(r) for r in g.rabbit_stocks]
        log.info(f"识别到 {len(g.rabbit_stocks)} 只兔子股票: {rabbit_names}")
        
        # 如果有兔子股票，识别乌龟股票
        if g.rabbit_stocks:
            # 识别乌龟股票（同板块同身位换手板）
            log.info('开始识别乌龟股票（同板块同身位换手板）')
            g.turtle_stocks = identify_turtle_stocks(context, g.rabbit_stocks)
            turtle_names = [format_stock_info(t) for t in g.turtle_stocks]
            log.info(f"识别到 {len(g.turtle_stocks)} 只乌龟股票: {turtle_names}")
            
            # 生成候选股票列表
            candidates = []
            for turtle in g.turtle_stocks:
                if turtle not in candidates:
                    candidates.append(turtle)
                    turtle_info = format_stock_info(turtle)
                    log.info(f'添加乌龟股票作为候选：{turtle_info}')
            
            # 限制候选股票数量
            max_candidates = 3  # 最多3只候选股票
            if len(candidates) > max_candidates:
                candidates = candidates[:max_candidates]
            
            candidates_with_names = [format_stock_info(c) for c in candidates]
            log.info(f'最终候选股票列表：{candidates_with_names}')
            
            g.stocks = candidates
        else:
            log.info('未识别到兔子股票，暂不使用龟兔模式')
            g.stocks = []
    else:
        log.info('情绪周期不是主升期，暂不使用龟兔模式')
        g.stocks = []
    
    # 计算风险指数
    risk_index = calculate_risk_index(context)
    
    # 检查是否持有股票，需要卖出的情况
    stocks_to_sell = []
    for stock in g.hold_stocks:
        try:
            # 龟兔模式卖出逻辑
            buy_date = g.buy_dates.get(stock, context.current_dt.date())
            hold_days = (context.current_dt.date() - buy_date).days
            
            # 获取当前价格
            current_price = 0
            try:
                current_data = get_current_data()[stock]
                if hasattr(current_data, 'price'):
                    current_price = current_data.price
            except:
                pass
            
            # 强制卖出条件：
            # 1. 持有超过2天（龟兔模式通常为短线操作）
            # 2. 股票不再是候选股票且持有超过1天
            # 3. 情绪周期转变
            # 4. 风险指数过高
            # 5. 止损条件：下跌超过5%
            # 6. 跌破5日均线（持续上涨的股票不破5日线不卖）
            sell_condition = False
            
            # 获取5日均线
            ma5 = 0
            try:
                hist_data = get_price(stock, end_date=context.current_dt.date(), count=5, frequency='daily', fields=['close'])
                if not hist_data.empty and len(hist_data) >= 5:
                    ma5 = hist_data['close'].mean()
            except:
                pass
            
            # 获取买入价格
            buy_price = g.buy_prices.get(stock, 0)
            
            # 判断是否盈利（持续上涨）
            is_profitable = current_price > buy_price if buy_price > 0 and current_price > 0 else False
            
            # 判断是否跌破5日均线
            below_ma5 = current_price < ma5 if ma5 > 0 and current_price > 0 else False
            
            if hold_days >= 2:
                # 如果持有超过2天，检查是否跌破5日均线
                if is_profitable and not below_ma5:
                    # 持续上涨且不破5日均线，继续持有
                    log.info(f'{format_stock_info(stock)} 持续上涨，不破5日均线(当前价:{current_price:.2f}, MA5:{ma5:.2f})，继续持有')
                    sell_condition = False
                else:
                    sell_condition = True
                    log.info(f'卖出条件1触发：持有超过2天且跌破5日均线或亏损')
            elif stock not in g.stocks and hold_days >= 1:
                # 如果股票不再是候选股票且持有超过1天，检查是否跌破5日均线
                if is_profitable and not below_ma5:
                    # 持续上涨且不破5日均线，继续持有
                    log.info(f'{format_stock_info(stock)} 持续上涨，不破5日均线(当前价:{current_price:.2f}, MA5:{ma5:.2f})，继续持有')
                    sell_condition = False
                else:
                    sell_condition = True
                    log.info(f'卖出条件2触发：股票不再是候选股票且持有超过1天，且跌破5日均线或亏损')
            elif g.emotion_cycle != 'uptrend':
                sell_condition = True
                log.info(f'卖出条件3触发：情绪周期转变')
            elif risk_index > 80:
                sell_condition = True
                log.info(f'卖出条件4触发：风险指数过高')
            
            # 止损条件
            if buy_price > 0 and current_price > 0:
                loss_ratio = (current_price - buy_price) / buy_price * 100
                if loss_ratio < -5:
                    sell_condition = True
                    log.info(f'卖出条件5触发：止损，下跌 {loss_ratio:.2f}%')
            
            if sell_condition:
                # 获取当前价格用于日志
                stock_info = format_stock_info(stock)
                try:
                    current_data = get_current_data()[stock]
                    if hasattr(current_data, 'price'):
                        current_price = current_data.price
                        buy_price = g.buy_prices.get(stock, 0)
                        day_return = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                        log.info(f'卖出条件触发，卖出 {stock_info}，持有天数: {hold_days}，当日收益: {day_return:.2f}%')
                    else:
                        log.info(f'卖出条件触发，卖出 {stock_info}，持有天数: {hold_days}')
                except Exception as e:
                    log.info(f'卖出条件触发，卖出 {stock_info}，持有天数: {hold_days}')
                
                stocks_to_sell.append(stock)
        except Exception as e:
            log.info(f"检查卖出条件失败: {e}")
            # 出错时也加入卖出列表，避免股票被卡住
            stocks_to_sell.append(stock)
    
    # 执行卖出操作
    for stock in stocks_to_sell:
        try:
            # 简化卖出操作，确保能够执行
            stock_info = format_stock_info(stock)
            log.info(f"执行卖出操作: {stock_info}")
            
            # 执行卖出
            order_target(stock, 0)
            
            # 强制更新持仓状态（不依赖卖出是否成功）
            if stock in g.hold_stocks:
                g.hold_stocks.remove(stock)
                log.info(f"从持仓中移除: {stock_info}")
            if stock in g.buy_prices:
                del g.buy_prices[stock]
            if stock in g.buy_dates:
                del g.buy_dates[stock]
                
            log.info(f"卖出操作完成: {stock_info}")
        except Exception as e:
            log.info(f"卖出操作失败: {e}")
            # 即使失败也尝试从持仓中移除，避免卡住
            if stock in g.hold_stocks:
                try:
                    g.hold_stocks.remove(stock)
                    log.info(f"强制从持仓中移除: {stock_info}")
                except:
                    pass
    
    # 买入逻辑 - 龟兔模式专用
    available_slots = g.max_hold_stocks - len(g.hold_stocks)
    if available_slots > 0 and g.stocks and g.emotion_cycle == 'uptrend':
        try:
            # 根据风险指数调整买入仓位
            cash = context.portfolio.available_cash
            if cash > 1000:
                # 根据风险指数调整仓位比例
                risk_adjusted_ratio = 0.95
                if risk_index > 70:
                    risk_adjusted_ratio = 0.7  # 高风险时降低仓位
                elif risk_index > 50:
                    risk_adjusted_ratio = 0.85  # 中等风险时适当降低仓位
                
                # 计算每只股票的买入金额
                buy_amount_per_stock = cash / available_slots * risk_adjusted_ratio
                
                log.info(f"风险调整后仓位比例: {risk_adjusted_ratio}, 每只股票买入金额: {buy_amount_per_stock}")
                
                # 选择候选股票（优先乌龟股票）
                buy_count = 0
                for target_stock in g.stocks:
                    if target_stock not in g.hold_stocks and buy_count < available_slots:
                        try:
                            # 检查股票是否可交易
                            security_info = get_security_info(target_stock)
                            if security_info and security_info.start_date <= context.current_dt.date() <= security_info.end_date:
                                # 检查是否为乌龟股票
                                if target_stock in g.turtle_stocks:
                                    # 龟兔模式买入逻辑
                                    target_stock_info = format_stock_info(target_stock)
                                    log.info(f'龟兔模式买入条件触发，买入 {target_stock_info}，金额：{buy_amount_per_stock}')
                                    order_value(target_stock, buy_amount_per_stock)
                                    # 买入成功后记录
                                    g.hold_stocks.append(target_stock)
                                    # 获取买入价格（使用最新价格）
                                    try:
                                        current_data = get_current_data()[target_stock]
                                        if hasattr(current_data, 'price'):
                                            g.buy_prices[target_stock] = current_data.price
                                        else:
                                            # 如果没有price属性，使用最新交易价格
                                            price_data = get_price(target_stock, end_date=context.current_dt, count=1, frequency='daily', fields=['close'])
                                            if not price_data.empty:
                                                g.buy_prices[target_stock] = price_data['close'].iloc[-1]
                                            else:
                                                g.buy_prices[target_stock] = 0
                                    except Exception as e:
                                        log.info(f"获取买入价格失败: {e}")
                                        g.buy_prices[target_stock] = 0
                                    g.buy_dates[target_stock] = context.current_dt.date()
                                    buy_count += 1
                                    log.info(f'龟兔模式买入完成: {target_stock_info}')
                                else:
                                    target_stock_info = format_stock_info(target_stock)
                                    log.info(f"{target_stock_info} 不是乌龟股票，跳过买入")
                            else:
                                target_stock_info = format_stock_info(target_stock)
                                log.info(f"股票 {target_stock_info} 不可交易")
                        except Exception as e:
                            target_stock_info = format_stock_info(target_stock)
                            log.info(f"买入 {target_stock_info} 失败: {e}")
        except Exception as e:
            log.info(f"买入操作失败: {e}")

## 收盘后运行函数
def after_market_close(context):
    log.info(str('函数运行时间(after_market_close):'+str(context.current_dt.time())))
    #得到当天所有成交记录
    trades = get_trades()
    for _trade in trades.values():
        log.info('成交记录：'+str(_trade))
    
    # 输出持仓信息（带股票名称）
    hold_stocks_with_names = [format_stock_info(s) for s in g.hold_stocks]
    log.info(f'当前持仓：{hold_stocks_with_names}')
    log.info(f'持仓数量：{len(g.hold_stocks)}/{g.max_hold_stocks}')
    
    log.info('一天结束')
    log.info('##############################################################')
