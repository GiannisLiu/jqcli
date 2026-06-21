# simple_rabbitisture.py
# 简化版：判断股票前一天一字封单后第二天是否继续一字封单
# 不依赖jqdata，使用模拟数据

import os
import json
from datetime import datetime, timedelta

# 本地数据存储路径
LOCAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'local_data')
os.makedirs(LOCAL_DATA_PATH, exist_ok=True)

# 本地缓存文件
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

# 模拟数据
def get_security_info(stock_code):
    """模拟获取股票信息"""
    class MockSecurity:
        def __init__(self):
            self.display_name = stock_code
            self.start_date = datetime(2000, 1, 1)
            self.end_date = datetime(2030, 12, 31)
    return MockSecurity()

def get_all_securities(types, date=None):
    """模拟获取所有股票"""
    class MockDataFrame:
        def __init__(self):
            self.index = ['000001.XSHE', '600519.XSHG', '000858.XSHE']
    return MockDataFrame()

def get_price(stock, start_date=None, end_date=None, frequency='daily', fields=['close']):
    """模拟获取股票价格"""
    class MockDataFrame:
        def __init__(self):
            self.close = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
            self.open = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
            self.high = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
            self.low = [99, 100, 101, 102, 103, 104, 105, 106, 107, 108]
            
        def __getitem__(self, key):
            return getattr(self, key)
        
        @property
        def empty(self):
            return False
        
        @property
        def iloc(self):
            class MockILoc:
                def __getitem__(self, index):
                    return self
                
                def __call__(self, index):
                    return 100
            return MockILoc()
    return MockDataFrame()

# 日志对象
class MockLog:
    def info(self, msg):
        print(f'[LOG] {msg}')

log = MockLog()

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

def is_limit_up_by_open(stock_code, date):
    """判断股票在指定日期是否为一字板（开盘涨停）"""
    try:
        # 加载本地缓存
        cache = load_local_cache()
        cache_key = f"{stock_code}_{date.strftime('%Y-%m-%d')}"
        
        # 检查缓存
        if cache_key in cache:
            cached_data = cache[cache_key]
            return cached_data['is_limit_up'], cached_data['open_price']
        
        # 获取前一天的收盘价
        prev_date = date - timedelta(days=1)
        hist_data = get_price(stock_code)
        
        if hist_data.empty:
            return False, 0
        
        prev_close = hist_data.close[-1]
        
        # 计算涨停价格
        limit_up_price = prev_close * 1.1
        limit_up_price = round(limit_up_price, 2)
        
        # 获取指定日期的开盘价
        today_data = get_price(stock_code)
        if not today_data.empty:
            today_open = today_data.open[-1]
        else:
            return False, 0
        
        # 判断是否开盘接近涨停
        is_limit_up = abs(today_open - limit_up_price) < 0.02
        
        # 保存到缓存
        cache[cache_key] = {
            'is_limit_up': is_limit_up,
            'open_price': today_open,
            'limit_up_price': limit_up_price,
            'prev_close': prev_close,
            'timestamp': date.strftime('%Y-%m-%d')
        }
        save_local_cache(cache)
        
        return is_limit_up, today_open
    except Exception as e:
        print(f"判断一字板失败: {e}")
        return False, 0

def check_continue_limit_up(stock_code, context=None, check_date=None):
    """检查股票前一天是否一字封单，且当天是否继续一字封单"""
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
    """扫描市场上连续一字封单的股票"""
    try:
        # 确定检查日期
        if check_date:
            current_date = check_date
        elif context and hasattr(context, 'current_dt'):
            current_date = context.current_dt.date()
        else:
            # 在本地环境中，使用当前日期
            current_date = datetime.now().date()
        
        # 获取所有股票
        try:
            all_stocks = get_all_securities(['stock']).index
        except Exception as e:
            # 使用默认股票列表进行测试
            all_stocks = ['000001.XSHE', '600519.XSHG', '000858.XSHE']
        
        continue_limit_up_stocks = []
        
        message = f"开始扫描连续一字封单股票，检查股票数量: {len(all_stocks)}"
        print(message)
        if context and hasattr(context, 'log'):
            context.log.info(message)
        
        # 检查所有股票
        for stock in all_stocks:
            try:
                # 检查股票是否可交易
                security_info = get_security_info(stock)
                if security_info:
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
    test_stock = '600519.XSHG'  # 贵州茅台
    test_date = datetime.now().date()
    
    print(f"\n测试股票: {format_stock_info(test_stock)}")
    print(f"测试日期: {test_date}")
    
    result, reason, prev_open, today_open = check_continue_limit_up(test_stock, check_date=test_date)
    print(f"\n测试结果: {result}")
    print(f"原因: {reason}")
    print(f"前一天开盘价: {prev_open}")
    print(f"当天开盘价: {today_open}")
    
    # 测试扫描功能
    print("\n===== 开始扫描市场 =====")
    continue_stocks = scan_continue_limit_up_stocks(check_date=test_date)
    
    if continue_stocks:
        print(f"\n发现连续一字封单股票: {[format_stock_info(s) for s in continue_stocks]}")
    else:
        print("\n未发现连续一字封单股票")
    
    print("\n===== 测试完成 =====")
