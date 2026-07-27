"""临时邮箱的测试：不连真实网络，用假邮件验证等待与提取逻辑是否正确。"""

from tools.mail import Mail                                 # 被测的临时邮箱工具


# 一个最小的假邮件对象，字段和 SDK 的 Email 对齐，供测试注入
class FakeMail:
    # --- 用给定字段构造一封假邮件 ---
    def __init__(self, id="1", subject="", text="", html="", from_addr=""):
        self.id = id                                       # 邮件唯一标识，用于新旧邮件去重
        self.subject = subject                             # 邮件主题
        self.text = text                                   # 纯文本正文
        self.html = html                                   # HTML 正文
        self.from_addr = from_addr                         # 发件人地址，用于来源过滤


# --- wait_code 能从新邮件里提取验证码 ---
def test_wait_code_extracts(monkeypatch):
    # monkeypatch 是 pytest 工具，用来临时替换对象的方法，从而避开真实网络
    mail = Mail.__new__(Mail)                              # 绕过 __init__ 造一个空壳实例，不触发网络配置
    mail.seen_ids = set()                                  # 手动补上 wait 逻辑需要的已读集合

    inbox = [FakeMail(id="a", subject="Verify", text="Your code is 246810")]  # 准备一封含验证码的假邮件
    monkeypatch.setattr(mail, "fetch", lambda: inbox)      # 把 fetch 换成直接返回这封假邮件

    code = mail.wait_code(timeout=2, interval=0)           # 等码，超时给 2 秒足够（假邮件立即返回）
    assert code == "246810"                                # 应挖出验证码


# --- wait_link 能按关键词挑出验证链接 ---
def test_wait_link_extracts(monkeypatch):
    mail = Mail.__new__(Mail)                              # 空壳实例
    mail.seen_ids = set()                                  # 补上已读集合

    body = '<a href="https://site.com/verify?t=9">click</a>'  # 含验证链接的 HTML 正文
    inbox = [FakeMail(id="b", subject="Hi", html=body)]    # 一封含链接的假邮件
    monkeypatch.setattr(mail, "fetch", lambda: inbox)      # fetch 返回这封假邮件

    link = mail.wait_link(keyword="verify", timeout=2, interval=0)  # 等含 verify 的链接
    assert link == "https://site.com/verify?t=9"           # 应挑出目标链接


# --- 收件箱一直为空时 wait_code 超时返回空串 ---
def test_wait_code_timeout(monkeypatch):
    mail = Mail.__new__(Mail)                              # 空壳实例
    mail.seen_ids = set()                                  # 补上已读集合
    monkeypatch.setattr(mail, "fetch", lambda: [])         # fetch 永远返回空收件箱

    code = mail.wait_code(timeout=1, interval=0)           # 短超时等待
    assert code == ""                                      # 等不到邮件应返回空串而非报错


# --- 已读过的旧邮件不会被当成新邮件重复处理 ---
def test_seen_ids_skip(monkeypatch):
    mail = Mail.__new__(Mail)                              # 空壳实例
    mail.seen_ids = {"old"}                                # 预先把 id 为 old 的邮件标记成已读

    inbox = [FakeMail(id="old", subject="Verify", text="code 111222")]  # 收件箱里只有那封旧邮件
    monkeypatch.setattr(mail, "fetch", lambda: inbox)      # fetch 返回旧邮件

    code = mail.wait_code(timeout=1, interval=0)           # 等新码
    assert code == ""                                      # 旧邮件应被跳过，等不到新码返回空串


# --- 等待邮件时可以按主题和发件人过滤 ---
def test_wait_mail_filters(monkeypatch):
    mail = Mail.__new__(Mail)                              # 空壳实例
    mail.seen_ids = set()                                  # 补上已读集合
    inbox = [
        FakeMail(id="a", subject="Welcome", from_addr="news@site.com"),
        FakeMail(id="b", subject="Verify account", from_addr="no-reply@site.com"),
    ]
    monkeypatch.setattr(mail, "fetch", lambda: inbox)      # 注入两封不同来源邮件
    found = mail.wait_mail(timeout=1, interval=0, subject="verify", sender="no-reply")
    assert found.id == "b"                                 # 应只返回同时符合主题和来源的邮件

    found = mail.wait_mail(timeout=1, interval=0, subject="welcome")  # 换一套条件再次等待
    assert found.id == "a"                                 # 上次未命中的邮件不应被永久吞掉


# --- 渠道列表支持模糊搜索 ---
def test_channels_search():
    items = Mail.channels("mailinator")                    # 按渠道名搜索，不发网络请求
    assert any("mailinator" in item.channel for item in items)  # 至少应找到官方 mailinator 渠道
