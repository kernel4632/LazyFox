"""
结果保存工具：把注册跑出来的账号、token 一行代码存进文件，之后还能读回来。

设计思想：
逆向注册批量跑账号，每成功一个就得把结果落盘，否则程序一停就全丢了。以前每个脚本
都自己写"打开文件、拼字符串、追加、换行"，既重复又容易忘记编码、忘记去重。这个文件
把保存和读取收敛成两种最常用形态：

1. 行式（Lines）——一行一条记录，适合纯 token 列表。自带去重，同一个 token 不会存两遍。
2. 表式（Table）——一条记录是一个字典，整体存成 JSON 数组，适合"邮箱+密码+token"这种多字段账号。

两者都做到"存之前先读旧的、合并、再整体写回"，所以随时中断都不会损坏文件。

里面有什么：
- Lines 类           管理"一行一条"的文本文件
- Lines.add()        追加一条，自动跳过重复
- Lines.read()       读回所有行
- Table 类           管理"一条一字典"的 JSON 文件
- Table.add()        追加一条账号记录
- Table.read()       读回所有账号

怎么调用：
    from tools.store import Lines, Table

    # 存纯 token
    tokens = Lines("tokens.txt")
    tokens.add("abc123")                  # 追加且自动去重

    # 存完整账号
    accounts = Table("accounts.json")
    accounts.add({"email": "a@b.com", "password": "x", "token": "abc123"})
    print(accounts.read())                # 读回全部账号列表
"""

import json                                                 # 用于把账号字典和 JSON 文件互相转换
import os                                                   # 原子替换临时文件，保证中断时原文件仍完整
import threading                                            # 同进程多个注册任务并发保存时需要互斥
from pathlib import Path                                   # 统一处理文件路径和父目录
from tempfile import NamedTemporaryFile                    # 在目标目录创建原子写入临时文件


class StoreError(RuntimeError):
    """结果文件已损坏或结构不对，阻止后续静默覆盖。"""


_locks = {}                                                 # 同一路径的所有保存对象共享一把锁
_locks_guard = threading.Lock()                             # 创建路径锁本身也需要互斥


# --- 获取某个结果文件的共享锁 ---
def _lock(path):
    key = str(path.resolve())                               # 绝对路径作为唯一标识，避免相对路径重复建锁
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.RLock()                # 第一次访问该路径时创建可重入锁
        return _locks[key]                                 # 后续 Lines/Table 实例复用同一把锁


class Lines:
    """管理一个"一行一条记录"的文本文件，自带去重。"""

    # --- 初始化：记住文件路径 ---
    def __init__(self, path):
        # path：文件保存路径，如 "tokens.txt"
        self.path = Path(path)                             # 统一保存为 Path，目录创建和替换更直觉
        self.lock = _lock(self.path)                       # 同路径的所有实例共享锁，保护“读→判断→写”整体

    # --- 读回文件里的所有行 ---
    def read(self):
        if not self.path.exists():                         # 文件还不存在说明一条都没存过
            return []                                       # 返回空列表，让调用方无需自己判空

        with self.path.open(encoding="utf-8") as file:    # 以 UTF-8 打开，兼容中文等非 ASCII 内容
            lines = file.read().splitlines()               # 按行拆开，splitlines 会自动去掉每行末尾换行符
        return [line for line in lines if line.strip()]    # 过滤掉空行，只返回有内容的行

    # --- 追加一条记录，重复则跳过 ---
    def add(self, value):
        # value：要保存的一行内容，如一个 token 字符串
        value = str(value).strip()                         # 统一转字符串并去掉首尾空白，避免因空格判成不同值
        if not value:                                      # 空内容没有保存意义，直接返回 False 表示没存
            return False

        with self.lock:                                    # 查重和写入必须原子化，避免两个线程同时写入同一 token
            lines = self.read()                            # 锁内读取最新内容
            if value in lines:                             # 已存在就不再重复保存
                return False
            lines.append(value)                            # 合并新 token
            _write(self.path, "\n".join(lines) + "\n")     # 原子写回整份文本，异常中断不会留下半行
        return True                                        # 返回 True 表示成功新增


class Table:
    """管理一个"一条记录是一个字典"的 JSON 文件，整体存成 JSON 数组。"""

    # --- 初始化：记住文件路径 ---
    def __init__(self, path):
        # path：文件保存路径，如 "accounts.json"
        self.path = Path(path)                             # 统一保存为 Path
        self.lock = _lock(self.path)                       # 同路径的所有实例共享锁

    # --- 读回所有账号记录 ---
    def read(self):
        if not self.path.exists():                         # 文件不存在说明还没存过任何账号
            return []                                       # 返回空列表

        try:
            with self.path.open(encoding="utf-8") as file:  # UTF-8 打开，兼容中文字段值
                data = json.load(file)                     # 把整个文件解析成 Python 对象
        except (json.JSONDecodeError, ValueError) as error:  # 文件被写坏时必须阻止下一次 add 覆盖原数据
            raise StoreError(f"结果文件已损坏: {self.path}") from error

        if not isinstance(data, list):                     # Table 只接受 JSON 数组
            raise StoreError(f"结果文件不是数组: {self.path}")
        return data                                        # 返回可信的账号列表

    # --- 追加一条账号记录 ---
    def add(self, record):
        # record：一条账号，是一个字典，如 {"email": ..., "password": ..., "token": ...}
        if not isinstance(record, dict):                   # 表式文件每条必须是字典，类型不对直接拒绝
            return False

        with self.lock:                                    # 保护“读旧数据→追加→写回”不被并发打断
            records = self.read()                          # 锁内读取最新账号列表
            records.append(record)                         # 把新账号追加到末尾
            text = json.dumps(records, ensure_ascii=False, indent=2) + "\n"  # 先在内存生成完整合法 JSON
            _write(self.path, text)                        # 原子替换，写到一半崩溃也不会破坏旧文件
        return True                                        # 返回 True 表示成功保存


# --- 原子写入：完整临时文件落盘后再替换目标 ---
def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)         # 保存目录不存在时自动创建
    temp_path = None                                       # 记录临时文件路径，失败时用于清理
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temp_path = Path(file.name)                    # 临时文件必须与目标同目录，os.replace 才保持原子性
            file.write(text)                               # 一次写入完整新内容
            file.flush()                                   # 把 Python 缓冲推给操作系统
            os.fsync(file.fileno())                        # 要求操作系统真正落盘后再替换
        os.replace(temp_path, path)                        # 原子替换目标文件
    finally:
        if temp_path and temp_path.exists():               # 替换前失败时清理遗留临时文件
            temp_path.unlink()
