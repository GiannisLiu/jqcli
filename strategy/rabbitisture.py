# rabbitisture.py
# 判断股票前一天一字封单后第二天是否继续一字封单
# 使用本地JQData SDK，去除jqdata依赖

import os
import json
from datetime import datetime, timedelta

# 使用本地JQData SDK
try:
    from jqdatasdk import *
    # 请替换为您的聚宽账号和密码
    auth('15538374228', 'Giannisliu0.')
    print('本地JQData SDK登录成功')
except ImportError:
    print('错误：未找到jqdatasdk模块')
    print('请按照以下步骤安装：')
    print('1. 安装JQData SDK: pip install jqdatasdk')
    print('2. 如果遇到thrifty2错误: pip install thriftpy2==0.4.20')
    print('3. 确保您的聚宽账号已开通数据权限')
    exit(1)
except Exception as e:
    print(f'错误：JQData SDK登录失败: {e}')
    print('请检查您的账号和密码是否正确')
    print('账号：申请时填写的手机号')
    print('密码：聚宽官网登录密码')
    exit(1)

# 本地数据存储路径
LOCAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'local_data')
os.makedirs(LOCAL_DATA_PATH, exist_ok=True)

# 本地数据存储路径
LIMIT_UP_CACHE_FILE = os.path.join(LOCAL_DATA_PATH, 'limit_up_cache.json')

# 缓存管理函数
def load_local_cache():
    """加载本地缓存"""
    try:
        if os.path.exists(LIMIT_UP_CACHE_FILE):
            with open(LIMIT_UP_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f'加载本地缓存失败: {e}')
    return {}

def save_local_cache(cache_data):
    """保存本地缓存"""
    try:
        with open(LIMIT_UP_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f'保存本地缓存失败: {e}')
        return False

# 日志函数
def log_info(msg):
    """日志输出函数"""
    print(f'[LOG] {msg}')

# 重命名为log对象，保持兼容性
class Log:
    def info(self, msg):
        log_info(msg)

log = Log()

# 核心功能函数
def get_stock_name(stock_code):
    """获取股票名称"""
    try:
        security_info = get_security_info(stock_code)
        if security_info and hasattr(security_info, 'display_name'):
            return security_info.display_name
        return ''
    except:
        return ''

def format_stock_info(stock_code):
    """格式化股票信息，返回'代码(名称)'格式"""
    name = get_stock_name(stock_code)
    if name:
        return f"{stock_code}({name})"
    return stock_code

def get_last_trading_day_data(stock_code, date, max_days_back=10):
    """获取最近有效交易日的数据
    
    Args:
        stock_code: 股票代码
        date: 起始日期
        max_days_back: 最大回溯天数
        
    Returns:
        (prev_close, prev_date) 或 (None, None)
    """
    for i in range(1, max_days_back + 1):
        prev_date = date - timedelta(days=i)
        prev_start = prev_date.replace(hour=9, minute=30, second=0)
        prev_end = prev_date.replace(hour=15, minute=0, second=0)
        
        prev_data = get_price(stock_code, start_date=prev_start, end_date=prev_end, 
                             frequency='daily', fields=['close'], round=True)
        
        if not prev_data.empty:
            return prev_data['close'].iloc[-1], prev_date
    
    return None, None

def is_limit_up_by_open(stock_code, date):
    """判断股票在指定日期是否为一字板（开盘涨停且全天封板，中间不开板）"""
    try:
        # 加载本地缓存
        cache = load_local_cache()
        cache_key = f"{stock_code}_{date.strftime('%Y-%m-%d')}"
        
        # 检查缓存
        if cache_key in cache:
            cached_data = cache[cache_key]
            return cached_data['is_limit_up'], cached_data['open_price']
        
        # 将日期转换为datetime格式（精确到时分秒）
        if isinstance(date, datetime):
            date_dt = date
        else:
            date_dt = datetime.combine(date, datetime.min.time())
        
        # 获取当天的开盘价、收盘价、最高价、最低价和成交量
        today_start = date_dt.replace(hour=9, minute=30, second=0)
        today_end = date_dt.replace(hour=15, minute=0, second=0)
        
        print(f"[DEBUG] 获取当天数据: 股票={stock_code}, 日期={date_dt.strftime('%Y-%m-%d')}")
        today_data = get_price(stock_code, start_date=today_start, end_date=today_end, 
                             frequency='daily', fields=['open', 'close', 'high', 'low', 'volume'], round=True)
        
        print(f"[DEBUG] 当天数据: {today_data}")
        if today_data.empty:
            print(f"[WARN] 当天数据为空，可能是非交易日或股票未上市")
            return False, 0
        
        today_open = today_data['open'].iloc[-1]
        today_close = today_data['close'].iloc[-1]
        today_high = today_data['high'].iloc[-1]
        today_low = today_data['low'].iloc[-1]
        today_volume = today_data['volume'].iloc[-1]
        
        print(f"[DEBUG] 开盘价={today_open}, 收盘价={today_close}, 最高价={today_high}, 最低价={today_low}, 成交量={today_volume}")
        
        # 获取最近有效交易日的收盘价
        print(f"[DEBUG] 查找最近有效交易日...")
        prev_close, prev_date = get_last_trading_day_data(stock_code, date_dt)
        
        if prev_close is None:
            print(f"[WARN] 无法找到有效的前一交易日数据")
            return False, 0
        
        print(f"[DEBUG] 前一交易日: {prev_date.strftime('%Y-%m-%d')}, 收盘价: {prev_close}")
        
        # 计算涨停价格
        limit_up_price = prev_close * 1.1
        limit_up_price = round(limit_up_price, 2)
        print(f"[DEBUG] 涨停价: {limit_up_price}")
        
        # 判断是否为一字涨停：
        # 1. 开盘价接近涨停价
        # 2. 收盘价接近涨停价（确保收盘封板）
        # 3. 最低价接近涨停价（排除中间开板的情况）
        # 4. 最高价接近涨停价（确保没有超过涨停价）
        is_open_limit = abs(today_open - limit_up_price) < 0.02
        is_close_limit = abs(today_close - limit_up_price) < 0.02
        is_low_limit = abs(today_low - limit_up_price) < 0.02
        is_high_limit = abs(today_high - limit_up_price) < 0.02
        
        print(f"[DEBUG] 开盘涨停={is_open_limit}, 收盘涨停={is_close_limit}, 最低涨停={is_low_limit}, 最高涨停={is_high_limit}")
        
        # 一字涨停：开盘、收盘、最高、最低都接近涨停价
        is_limit_up = is_open_limit and is_close_limit and is_low_limit and is_high_limit
        print(f"[DEBUG] 是否一字涨停: {is_limit_up}")
        
        # 保存到缓存（转换为Python原生类型，避免JSON序列化问题）
        cache[cache_key] = {
            'is_limit_up': bool(is_limit_up),
            'open_price': float(today_open),
            'close_price': float(today_close),
            'high_price': float(today_high),
            'low_price': float(today_low),
            'limit_up_price': float(limit_up_price),
            'prev_close': float(prev_close),
            'volume': float(today_volume),
            'timestamp': date.strftime('%Y-%m-%d')
        }
        save_local_cache(cache)
        
        return is_limit_up, today_open
    except Exception as e:
        print(f"判断一字板失败: {e}")
        import traceback
        traceback.print_exc()
        return False, 0

def check_continue_limit_up(stock_code, context=None, check_date=None):
    """检查股票前一天是否一字封单，且当天是否继续一字封单
    
    Args:
        stock_code: 股票代码
        context: 上下文对象（可选）
        check_date: 检查日期（可选）
    """
    try:
        # 确定检查日期
        if check_date:
            current_date = check_date
        elif context and hasattr(context, 'current_dt'):
            current_date = context.current_dt.date()
        else:
            # 在本地环境中，使用当前日期
            current_date = datetime.now().date()
        
        # 检查前一天是否一字封单
        prev_date = current_date - timedelta(days=1)
        prev_limit_up, prev_open_price = is_limit_up_by_open(stock_code, prev_date)
        
        if not prev_limit_up:
            return False, "前一天不是一字封单", 0, 0
        
        # 检查当天是否继续一字封单
        today_limit_up, today_open_price = is_limit_up_by_open(stock_code, current_date)
        
        stock_info = format_stock_info(stock_code)
        
        if today_limit_up:
            message = f"{stock_info} 连续一字封单: 前一天开盘价={prev_open_price}, 当天开盘价={today_open_price}"
            print(message)
            if context and hasattr(context, 'log'):
                context.log.info(message)
            return True, "连续一字封单", prev_open_price, today_open_price
        else:
            message = f"{stock_info} 前一天是一字封单，但当天未继续: 前一天开盘价={prev_open_price}"
            print(message)
            if context and hasattr(context, 'log'):
                context.log.info(message)
            return False, "当天未继续一字封单", prev_open_price, today_open_price
    except Exception as e:
        error_message = f"检查连续一字封单失败: {e}"
        print(error_message)
        if context and hasattr(context, 'log'):
            context.log.info(error_message)
        return False, "检查失败", 0, 0

def scan_continue_limit_up_stocks(context=None, check_date=None):
    """扫描市场上连续一字封单的股票
    
    Args:
        context: 上下文对象（可选）
        check_date: 检查日期（可选）
    """
    try:
        # 确定检查日期
        if check_date:
            current_date = check_date
        elif context and hasattr(context, 'current_dt'):
            current_date = context.current_dt.date()
        else:
            # 在本地环境中，使用当前日期
            current_date = datetime.now().date()
        
        # 使用JQData标准接口获取所有股票
        # get_all_securities支持获取股票、基金、债券等多种类型
        all_securities = get_all_securities(['stock'], date=current_date)
        all_stocks = all_securities.index.tolist()
        
        continue_limit_up_stocks = []
        
        message = f"开始扫描连续一字封单股票，检查股票数量: {len(all_stocks)}"
        print(message)
        if context and hasattr(context, 'log'):
            context.log.info(message)
        
        # 检查所有股票
        for stock in all_stocks:
            try:
                # 使用JQData标准接口获取股票信息
                security_info = get_security_info(stock)
                if security_info:
                    # 检查股票是否在交易时间范围内
                    if security_info.start_date <= current_date <= security_info.end_date:
                        result, reason, prev_open, today_open = check_continue_limit_up(stock, context, current_date)
                        if result:
                            stock_info = format_stock_info(stock)
                            continue_limit_up_stocks.append((stock, stock_info, prev_open, today_open))
                            found_message = f"发现连续一字封单股票: {stock_info}"
                            print(found_message)
                            if context and hasattr(context, 'log'):
                                context.log.info(found_message)
            except Exception as e:
                continue
        
        finish_message = f"扫描完成，发现 {len(continue_limit_up_stocks)} 只连续一字封单股票"
        print(finish_message)
        if context and hasattr(context, 'log'):
            context.log.info(finish_message)
        
        for stock, stock_info, prev_open, today_open in continue_limit_up_stocks:
            stock_message = f"{stock_info}: 前一天开盘={prev_open}, 当天开盘={today_open}"
            print(stock_message)
            if context and hasattr(context, 'log'):
                context.log.info(stock_message)
        
        return [stock for stock, _, _, _ in continue_limit_up_stocks]
    except Exception as e:
        error_message = f"扫描连续一字封单股票失败: {e}"
        print(error_message)
        if context and hasattr(context, 'log'):
            context.log.info(error_message)
        return []

# 本地测试功能
if __name__ == "__main__":
    print("===== 本地测试模式 =====")
    
    # 测试单只股票
    test_stock = '000592.XSHE'  # 平潭发展
    
    # 注意：JQData试用账号只能获取"前15个月~前3个月"的数据
    # 请使用有效的历史交易日进行测试
    # 例如：2024年6月1日（确保在试用账号的数据范围内）
    test_date = datetime(2025, 10, 28, 9, 30, 0)  # 使用一个有效的历史交易日
    

    prev_data = get_price(test_stock, start_date=test_date, end_date=test_date, 
                           frequency='daily', fields=['close'], round=True)
        
    print(f"[DEBUG] 前一天数据: {prev_data}")


    print(f"\n测试股票: {format_stock_info(test_stock)}")
    print(f"测试日期: {test_date}")
    
    result, reason, prev_open, today_open = check_continue_limit_up(test_stock, check_date=test_date)
    print(f"\n测试结果: {result}")
    print(f"原因: {reason}")
    print(f"前一天开盘价: {prev_open}")
    print(f"当天开盘价: {today_open}")
    
    # 测试扫描功能
    # print("\n===== 开始扫描市场 =====")
    # continue_stocks = scan_continue_limit_up_stocks(check_date=test_date)
    
    # if continue_stocks:
    #     print(f"\n发现连续一字封单股票: {[format_stock_info(s) for s in continue_stocks]}")
    # else:
    #     print("\n未发现连续一字封单股票")
    
    print("\n===== 测试完成 =====")

# 兼容聚宽平台的函数（如果需要）
def initialize(context):
    """聚宽平台初始化函数"""
    print("聚宽平台初始化")

def scan_continue_limit_up(context):
    """聚宽平台扫描函数"""
    print(f"开始扫描连续一字封单股票，当前时间: {datetime.now()}")
    continue_limit_up_stocks = scan_continue_limit_up_stocks(context)
    
    if continue_limit_up_stocks:
        print(f"今日连续一字封单股票: {[format_stock_info(s) for s in continue_limit_up_stocks]}")
    else:
        print("今日未发现连续一字封单股票")
