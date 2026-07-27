"""
日志工具：统一输出清楚、带时间和颜色的运行信息，不让每个脚本重复配置 logging/Rich。

设计思想：
注册机和逆向代理都需要看运行进度。底层使用标准 logging 保存生态兼容性，终端输出交给
RichHandler 负责颜色、异常堆栈和 Windows 兼容。调用方只创建一次 Log，之后直接使用
debug/info/warning/error/exception；需要落盘时调用 file。

怎么调用：
    from lazyfox import Log

    log = Log("grok")
    log.info("开始注册")
    try:
        run()
    except Exception:
        log.exception("注册失败")

    log.file("logs/grok.log")               # 后续同时写入文件
"""

import logging                                              # 标准日志核心，负责级别、处理器和第三方兼容
from pathlib import Path                                   # 创建日志文件父目录

from rich.logging import RichHandler                       # Rich 彩色终端处理器，负责美化和异常堆栈


class Log:
    """彩色日志入口，方法名和标准 logging 保持一致。"""

    # --- 建立一个不会重复输出的日志器 ---
    def __init__(self, name="app", level=logging.INFO, reset=False):
        # name：日志来源名称，同名实例会复用同一个底层 logger
        # level：最低输出级别，默认 INFO
        # reset：是否清掉同名 logger 的旧处理器，测试或重新配置时使用
        self.name = name                                    # 保存名称，文件日志格式会展示
        self.level = level                                  # 保存当前级别，新增文件处理器时沿用
        self.log = logging.getLogger(name)                  # 获取标准 logger，方便第三方库接入
        self.log.setLevel(level)                            # 设置 logger 总级别
        self.log.propagate = False                          # 禁止向根 logger 传播，避免同一条打印两次

        if reset:
            self.log.handlers.clear()                      # 调用方明确要求时移除旧输出通道
        if not self.log.handlers:
            self.log.addHandler(self._console(level))      # 第一次创建该名称时装上彩色终端输出

    # --- 构造彩色终端输出 ---
    def _console(self, level):
        handler = RichHandler(                             # Rich 负责颜色、时间和异常堆栈排版
            level=level,
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            markup=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))  # Rich 已展示时间/级别，只保留正文
        return handler

    # --- 增加纯文本文件输出 ---
    def file(self, path, level=None):
        # path：日志文件路径，父目录不存在时自动创建
        # level：该文件最低级别，不传就沿用 Log 当前级别
        file_path = Path(path)                             # 统一转 Path 方便创建目录
        file_path.parent.mkdir(parents=True, exist_ok=True)  # 确保 logs/ 之类目录存在
        handler = logging.FileHandler(file_path, encoding="utf-8")  # UTF-8 写文件，不含终端颜色码
        handler.setLevel(self.level if level is None else level)  # 文件可单独设置更高门槛
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        self.log.addHandler(handler)                       # 加到当前 logger，后续日志同时输出终端和文件
        return self                                        # 返回自身，支持 Log(...).file(...) 链式写法

    # --- 修改所有输出通道的最低级别 ---
    def set_level(self, level):
        self.level = level                                 # 更新实例记录
        self.log.setLevel(level)                           # 更新 logger 总级别
        for handler in self.log.handlers:
            handler.setLevel(level)                       # 已安装的终端/文件处理器同步更新
        return self

    # --- DEBUG 调试信息 ---
    def debug(self, message, *args, **kwargs):
        self.log.debug(message, *args, **kwargs)           # 转交标准 logger，保留格式化参数能力

    # --- INFO 正常进度 ---
    def info(self, message, *args, **kwargs):
        self.log.info(message, *args, **kwargs)            # 输出正常业务进度

    # --- WARNING 可继续的异常情况 ---
    def warning(self, message, *args, **kwargs):
        self.log.warning(message, *args, **kwargs)         # 输出警告但不终止流程

    # --- ERROR 已失败的动作 ---
    def error(self, message, *args, **kwargs):
        self.log.error(message, *args, **kwargs)           # 输出错误信息

    # --- CRITICAL 系统级严重错误 ---
    def critical(self, message, *args, **kwargs):
        self.log.critical(message, *args, **kwargs)        # 输出最高级别错误

    # --- 输出错误并附带当前异常堆栈 ---
    def exception(self, message, *args, **kwargs):
        self.log.exception(message, *args, **kwargs)       # 在 except 块里使用，Rich 会格式化完整堆栈

    # --- 交出底层标准 logger 给第三方库 ---
    def raw(self):
        return self.log                                    # 某些框架只接受 logging.Logger，直接传这个结果
