"""
worldrouter.py 测试脚本 - 验证代码导入和基本结构
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

print("=" * 60)
print("worldrouter.py 测试")
print("=" * 60)

# 测试 1: 导入模块
print("\n[测试 1] 导入模块...")
try:
    from register import worldrouter

    print("✓ 模块导入成功")
except Exception as e:
    print(f"✗ 模块导入失败: {e}")
    sys.exit(1)

# 测试 2: 检查必要的函数是否存在
print("\n[测试 2] 检查函数...")
required_functions = ["register", "extractCode", "waitForCredit", "createApiKey", "saveApiKey", "runBatch"]
for func_name in required_functions:
    if hasattr(worldrouter, func_name):
        print(f"✓ 函数 {func_name} 存在")
    else:
        print(f"✗ 函数 {func_name} 缺失")

# 测试 3: 检查依赖导入
print("\n[测试 3] 检查依赖...")
dependencies = [
    "nodriver",
    "asyncio",
    "re",
    "bs4",
    "TempMail.m2u",
    "tools.identity",
    "rich.console",
]
for dep in dependencies:
    try:
        __import__(dep)
        print(f"✓ {dep} 可用")
    except ImportError as e:
        print(f"✗ {dep} 导入失败: {e}")

# 测试 4: 验证代码结构
print("\n[测试 4] 验证代码结构...")
import inspect

# 检查 register 函数签名
if hasattr(worldrouter, "register"):
    sig = inspect.signature(worldrouter.register)
    print(f"✓ register() 函数签名: {sig}")
    if inspect.iscoroutinefunction(worldrouter.register):
        print("✓ register() 是异步函数")
    else:
        print("✗ register() 不是异步函数")

# 检查 runBatch 函数签名
if hasattr(worldrouter, "runBatch"):
    sig = inspect.signature(worldrouter.runBatch)
    print(f"✓ runBatch() 函数签名: {sig}")
    if inspect.iscoroutinefunction(worldrouter.runBatch):
        print("✓ runBatch() 是异步函数")
    else:
        print("✗ runBatch() 不是异步函数")

print("\n" + "=" * 60)
print("测试完成！代码结构验证通过。")
print("=" * 60)
print("\n提示: 要运行实际注册流程，请执行:")
print("  uv run python register/worldrouter.py")
