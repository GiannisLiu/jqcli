# test_jqdata.py
# 测试JQData SDK是否安装成功

try:
    from jqdatasdk import *
    print('✅ JQData SDK导入成功')
    print('JQData SDK已安装完成，可以使用了')
    print('\n使用说明：')
    print('1. 在rabbitisture.py中取消注释auth行')
    print('2. 填写您的聚宽账号和密码')
    print('3. 重新运行代码')
except ImportError as e:
    print('❌ JQData SDK导入失败:', e)
    print('请检查安装是否正确')
except Exception as e:
    print('❌ 其他错误:', e)
