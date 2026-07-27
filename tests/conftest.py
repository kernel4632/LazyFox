"""pytest 启动配置：把项目根目录加进模块搜索路径，让测试能直接 import tools 包。"""

import sys                                                  # 用于操作 Python 的模块搜索路径
import pathlib                                              # 用于定位项目根目录

# 本文件位于 tests/ 下，它的父目录就是项目根，把根目录插到搜索路径最前面
# 这样 tests 里写 from tools.xxx import 就能找到，不必每次手动设置 PYTHONPATH
root = pathlib.Path(__file__).resolve().parent.parent      # 从本文件往上两级得到项目根目录
sys.path.insert(0, str(root))                              # 插到最前，确保优先从项目根解析 tools 包
