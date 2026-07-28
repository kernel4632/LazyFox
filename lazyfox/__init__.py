"""
LazyFox 顶层入口：常用工具按需导入，示例不关心内部文件位置。

推荐写法：
    from lazyfox import Browser, Mail, Person, Proxy, HTTP, Log

按需导入很重要：只写 reverse/ HTTP 代理时不会加载 nodriver 和临时邮箱；只写 register/
时也不会加载 FastAPI。某个可选能力的环境有问题，不会阻断其他能力。
"""

from importlib import import_module                         # 第一次访问公开名字时才加载对应模块


# 公开名字到“模块, 属性”的映射；这里是顶层 API 的唯一真源。
exports = {
    "AsyncHTTP": ("tools.http", "AsyncHTTP"),
    "Browser": ("tools.browser", "Browser"),
    "Event": ("tools.sse", "Event"),
    "HTTP": ("tools.http", "HTTP"),
    "Field": ("tools.form", "Field"),
    "Finding": ("tools.probe", "Finding"),
    "Form": ("tools.form", "Form"),
    "Lines": ("tools.store", "Lines"),
    "Log": ("tools.log", "Log"),
    "Mail": ("tools.mail", "Mail"),
    "Person": ("tools.identity", "Person"),
    "Proxy": ("tools.proxy", "Proxy"),
    "Result": ("tools.form", "Result"),
    "Case": ("tools.probe", "Case"),
    "StoreError": ("tools.store", "StoreError"),
    "Table": ("tools.store", "Table"),
    "Turnstile": ("tools.turnstile", "Turnstile"),
    "find_code": ("tools.code", "find_code"),
    "find_link": ("tools.code", "find_link"),
    "first_form": ("tools.form", "first_form"),
    "forms": ("tools.form", "forms"),
    "form_cases": ("tools.probe", "form_cases"),
    "groups": ("tools.probe", "groups"),
    "result": ("tools.form", "result"),
    "run_probes": ("tools.probe", "run"),
    "parse": ("tools.sse", "parse"),
    "parse_async": ("tools.sse", "parse_async"),
    "strip_tags": ("tools.code", "strip_tags"),
    "turnstile_attach": ("tools.turnstile", "attach"),
    "turnstile_audit": ("tools.turnstile", "audit"),
    "turnstile_dummy": ("tools.turnstile", "dummy"),
    "turnstile": ("tools.form", "turnstile"),
}

__all__ = list(exports)                                     # IDE 和 from lazyfox import * 使用同一份公开名单


# --- 第一次访问公开工具时加载并缓存 ---
def __getattr__(name):
    if name not in exports:                                 # 未公开名字遵循 Python 标准行为
        raise AttributeError(f"module 'lazyfox' has no attribute {name!r}")

    module_name, attr_name = exports[name]                  # 找到真实模块和属性名
    try:
        value = getattr(import_module(module_name), attr_name)  # 只加载使用者真正请求的能力
    except ImportError as error:
        raise ImportError(f"LazyFox 无法加载 {name}，请先运行 uv sync；原始错误: {error}") from error
    globals()[name] = value                                 # 缓存结果，后续访问和普通导入一样快
    return value                                            # 把目标类或函数交给调用方


# --- 告诉 dir() 和 IDE 当前模块有哪些公开名字 ---
def __dir__():
    return sorted(set(globals()) | set(exports))            # 合并模块内置名字和惰性公开名字
