"""顶层入口测试：确认使用者可以从 lazyfox 一次导入全部核心工具。"""

import subprocess                                            # 在全新 Python 进程验证惰性导入，不受测试顺序影响
import sys                                                   # 取得当前测试解释器路径

import lazyfox                                               # 被测的公开顶层包


def test_public_tools_exist():
    names = ["Browser", "Mail", "Person", "Proxy", "HTTP", "AsyncHTTP", "Log", "Lines", "Table", "StoreError"]
    for name in names:                                      # 逐个检查 README 承诺的公开工具
        assert hasattr(lazyfox, name)                       # 缺任何一个都说明顶层出口不完整


def test_import_is_lazy():
    code = "import sys, lazyfox; assert 'tools.browser' not in sys.modules; assert 'tools.proxy' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)  # 全新进程只 import lazyfox
    assert result.returncode == 0, result.stderr            # 顶层导入不应加载浏览器或 FastAPI 代理
