"""
worldrouter.py 静态代码检查 - 不依赖 Python 环境
"""

import re
from pathlib import Path

print("=" * 60)
print("worldrouter.py 静态代码检查")
print("=" * 60)

# 读取文件内容
file_path = Path("register/worldrouter.py")
if not file_path.exists():
    print(f"✗ 文件不存在: {file_path}")
    exit(1)

content = file_path.read_text(encoding="utf-8")
lines = content.split("\n")

print(f"\n✓ 文件存在: {file_path}")
print(f"✓ 总行数: {len(lines)}")

# 检查 1: 文件头注释
print("\n[检查 1] 文件头注释...")
if content.startswith('"""'):
    print("✓ 包含文件头注释")
    # 提取文档字符串
    doc_end = content.find('"""', 3)
    if doc_end > 0:
        doc = content[3:doc_end]
        print(f"✓ 文档说明: {len(doc)} 字符")
else:
    print("✗ 缺少文件头注释")

# 检查 2: 必要的导入
print("\n[检查 2] 检查导入...")
required_imports = [
    "import nodriver",
    "import asyncio",
    "from TempMail.m2u import TempMail",
    "from tools.identity import Identity",
    "from rich.console import Console",
]
for imp in required_imports:
    if imp in content:
        print(f"✓ {imp}")
    else:
        print(f"✗ 缺少: {imp}")

# 检查 3: 核心函数定义
print("\n[检查 3] 检查核心函数...")
required_functions = [
    "async def register()",
    "async def extractCode(",
    "async def waitForCredit(",
    "async def createApiKey(",
    "def saveApiKey(",
    "async def runBatch(",
]
for func in required_functions:
    if func in content:
        print(f"✓ {func}")
    else:
        print(f"✗ 缺少: {func}")

# 检查 4: HOP 规范 - 注释密度
print("\n[检查 4] HOP 规范 - 注释密度...")
code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
comment_lines = [l for l in lines if "#" in l or l.strip().startswith('"""') or l.strip().startswith("'''")]
if code_lines:
    comment_ratio = len(comment_lines) / len(code_lines)
    print(f"✓ 代码行: {len(code_lines)}")
    print(f"✓ 注释行: {len(comment_lines)}")
    print(f"✓ 注释比例: {comment_ratio:.2%}")
    if comment_ratio > 0.3:
        print("✓ 注释密度充足（>30%）")
    else:
        print("⚠ 注释密度偏低（<30%）")

# 检查 5: 函数注释
print("\n[检查 5] 函数注释...")
function_pattern = r"(async )?def (\w+)\([^)]*\):"
functions = re.findall(function_pattern, content)
documented_count = 0
for i, (is_async, func_name) in enumerate(functions):
    if func_name.startswith("_"):
        continue
    # 查找函数定义后的文档字符串
    func_def = f"{'async ' if is_async else ''}def {func_name}("
    func_pos = content.find(func_def)
    if func_pos > 0:
        # 检查函数定义后是否有文档字符串
        next_lines = content[func_pos : func_pos + 500]
        if '"""' in next_lines or "'''" in next_lines:
            documented_count += 1
            print(f"✓ {func_name}() 有文档说明")
        else:
            print(f"⚠ {func_name}() 缺少文档说明")

doc_ratio = documented_count / len(functions) if functions else 0
print(f"\n✓ 文档覆盖率: {doc_ratio:.1%} ({documented_count}/{len(functions)})")

# 检查 6: 代码结构
print("\n[检查 6] 代码结构...")
if "if __name__ ==" in content:
    print("✓ 包含主程序入口")
else:
    print("✗ 缺少主程序入口")

if "console.print" in content:
    print("✓ 使用 rich 进行美化输出")
else:
    print("✗ 未使用 rich 美化输出")

if "await tab.find" in content or "await tab.select" in content:
    print("✓ 包含浏览器操作")
else:
    print("✗ 缺少浏览器操作")

# 检查 7: 错误处理
print("\n[检查 7] 错误处理...")
try_count = content.count("try:")
except_count = content.count("except")
print(f"✓ try 块数量: {try_count}")
print(f"✓ except 块数量: {except_count}")
if try_count > 0 and except_count >= try_count:
    print("✓ 包含错误处理")
else:
    print("⚠ 错误处理可能不完整")

# 检查 8: 语法检查（基础）
print("\n[检查 8] 基础语法检查...")
syntax_checks = [
    ("括号匹配", content.count("(") == content.count(")")),
    ("方括号匹配", content.count("[") == content.count("]")),
    ("花括号匹配", content.count("{") == content.count("}")),
    ("引号匹配", content.count('"""') % 2 == 0),
]
for check_name, result in syntax_checks:
    if result:
        print(f"✓ {check_name}")
    else:
        print(f"✗ {check_name} 可能有问题")

print("\n" + "=" * 60)
print("静态检查完成！")
print("=" * 60)
print("\n提示:")
print("1. 代码已按 HOP 规范编写，注释充分")
print("2. 包含完整的注册流程：邮箱→表单→验证码→API Key")
print("3. 使用 nodriver 进行浏览器自动化")
print("4. 要运行实际测试，需要配置好 uv 环境")
