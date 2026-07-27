"""结果保存的测试：确认行式去重、表式追加、读回都正确（用临时文件不污染项目）。"""

import threading                                            # 模拟多个注册任务并发保存

import pytest                                               # 验证损坏文件会明确抛错

from tools.store import Lines, StoreError, Table            # 被测保存器和损坏错误


# --- 行式：追加与读回 ---
def test_lines_add_and_read(tmp_path):
    # tmp_path 是 pytest 提供的临时目录，测试结束自动清理，不留垃圾文件
    path = tmp_path / "tokens.txt"                         # 临时 token 文件
    store = Lines(str(path))                               # 创建行式保存器

    assert store.add("abc") is True                        # 首次添加应成功
    assert store.add("def") is True                        # 添加第二条也成功
    assert store.read() == ["abc", "def"]                  # 读回应按写入顺序返回两条


# --- 行式：重复项自动跳过 ---
def test_lines_dedup(tmp_path):
    path = tmp_path / "tokens.txt"                         # 临时文件
    store = Lines(str(path))
    store.add("abc")                                       # 先存一条
    assert store.add("abc") is False                       # 再存同一条应被拒绝
    assert store.read() == ["abc"]                         # 文件里仍只有一条


# --- 行式：文件不存在时读回空列表 ---
def test_lines_read_missing(tmp_path):
    store = Lines(str(tmp_path / "nope.txt"))              # 指向一个不存在的文件
    assert store.read() == []                              # 应安全返回空列表


# --- 表式：追加账号与读回 ---
def test_table_add_and_read(tmp_path):
    path = tmp_path / "accounts.json"                      # 临时账号文件
    store = Table(str(path))                               # 创建表式保存器

    store.add({"email": "a@b.com", "token": "x"})          # 存第一条账号
    store.add({"email": "c@d.com", "token": "y"})          # 存第二条账号
    records = store.read()                                  # 读回全部
    assert len(records) == 2                                # 应有两条
    assert records[0]["email"] == "a@b.com"                # 第一条内容正确


# --- 表式：非字典记录被拒绝 ---
def test_table_reject_non_dict(tmp_path):
    store = Table(str(tmp_path / "accounts.json"))
    assert store.add("not a dict") is False                # 传字符串应被拒绝
    assert store.read() == []                              # 文件里不该有任何记录


# --- 表式：损坏文件必须报错并保留原内容 ---
def test_table_read_broken(tmp_path):
    path = tmp_path / "accounts.json"                      # 临时文件
    path.write_text("{ this is not valid json", encoding="utf-8")  # 故意写入坏内容
    store = Table(str(path))
    with pytest.raises(StoreError):
        store.add({"email": "new@site.com"})               # 禁止把坏文件当空库覆盖
    assert path.read_text(encoding="utf-8") == "{ this is not valid json"  # 原始损坏现场保持不变


# --- 多个保存对象并发写同一文件不会丢记录 ---
def test_table_shared_lock(tmp_path):
    path = tmp_path / "accounts.json"                      # 所有实例指向同一结果文件
    stores = [Table(path) for _ in range(10)]              # 模拟十个独立注册任务
    threads = [
        threading.Thread(target=store.add, args=({"id": index},))
        for index, store in enumerate(stores)
    ]
    for thread in threads:
        thread.start()                                     # 并发发起保存
    for thread in threads:
        thread.join()                                      # 等全部保存结束
    assert len(Table(path).read()) == 10                   # 十条都必须保留，不能互相覆盖
