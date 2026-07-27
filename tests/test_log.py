"""日志测试：确认文件通道会创建目录并写入纯文本内容。"""

import logging                                              # 使用标准日志级别常量

from tools.log import Log                                   # 被测日志工具


def test_log_writes_file(tmp_path):
    path = tmp_path / "logs" / "run.log"                   # 父目录一开始不存在
    log = Log("test-file-log", level=logging.INFO, reset=True).file(path)  # 建立终端+文件通道
    log.info("hello lazyfox")                               # 写一条测试信息
    for handler in log.raw().handlers:
        handler.flush()                                     # 强制文件处理器落盘，避免缓冲影响断言
    text = path.read_text(encoding="utf-8")                 # 读取日志文件
    assert "hello lazyfox" in text                          # 正文应被写入
