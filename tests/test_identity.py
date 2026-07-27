"""身份生成的测试：确认字段齐全、种子可复现、语言切换正常。"""

from datetime import date                                  # 验证年龄与生日是否一致

from tools.identity import Person                           # 被测的假身份生成器


# --- 全套字段都在且非空 ---
def test_all_fields_present():
    data = Person(seed=1).all()                            # 造一份完整资料
    for key in ["name", "first", "last", "user", "password", "email", "phone", "age", "birthday", "city", "company", "agent"]:
        assert key in data                                 # 每个约定字段都必须存在
        assert data[key] not in ("", None)                 # 且都不能是空值


# --- 同一种子结果可复现 ---
def test_seed_reproducible():
    first = Person(seed=42).all()                          # 用种子 42 造一份
    second = Person(seed=42).all()                         # 再用同样种子造一份
    assert first == second                                 # 两份应完全一致，证明可复现


# --- 不同种子结果应不同 ---
def test_different_seed_differs():
    a = Person(seed=1).all()                               # 种子 1
    b = Person(seed=2).all()                               # 种子 2
    assert a != b                                          # 不同种子应造出不同身份


# --- 密码长度可控且够强 ---
def test_password_length():
    pw = Person(seed=3).password(length=16)                # 指定 16 位密码
    assert len(pw) == 16                                   # 长度应精确匹配


# --- 邮箱格式合理 ---
def test_email_shape():
    email = Person(seed=4).email()                         # 造一个邮箱
    assert "@" in email and "." in email                   # 至少要有 @ 和域名点号


# --- 中文语言可用 ---
def test_chinese_locale():
    who = Person(lang="zh", seed=5)                        # 造中文身份
    assert who.name()                                      # 中文姓名应能正常生成且非空


# --- 全套身份中的关联字段必须互相一致 ---
def test_all_fields_are_consistent():
    data = Person(seed=9).all()                            # 生成同一份完整身份
    assert data["name"] == f"{data['first']} {data['last']}"  # 英文全名必须由同一组名和姓组成
    born = date.fromisoformat(data["birthday"])            # 解析生日
    today = date.today()                                   # 按当前日期计算应有年龄
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    assert data["age"] == age                              # 年龄必须和生日匹配
