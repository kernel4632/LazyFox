"""Grok 示例测试：用假浏览器和假邮箱离线验证完整注册流程，不访问真实 x.ai。"""

import pytest                                               # 验证封禁页会给出明确异常

from register.grok import EmailRejected, EmailUnavailable, SELECTORS, Settings, main, make_email, parse_args, register, reject_blocked, settings_from, wait_challenge, wait_registration  # 被测流程边界
from tools.store import Lines                              # 读取流程写出的 token 文件


class FakeMail:
    """立即返回邮箱和验证码的假邮箱。"""

    def __init__(self, channel=None, proxy=None, timeout=20):
        self.error = ""                                    # 与真实 Mail 的诊断字段保持一致

    def create(self, **kwargs):
        return "demo@mail.test"                            # 固定合法邮箱，流程无需真实网络

    def wait_code(self, **kwargs):
        return "ABC-123"                                   # 覆盖 x.ai 常见分段验证码

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakePerson:
    """返回固定且关联一致的注册身份。"""

    def __init__(self, lang="en"):
        self.lang = lang

    def all(self):
        return {
            "name": "Ada Stone",
            "first": "Ada",
            "last": "Stone",
            "password": "GoodPass1!",
        }


class FakeBrowser:
    """记录动作并始终成功的假浏览器。"""

    last = None                                            # 最近创建的实例，测试结束后检查动作记录

    def __init__(self, **kwargs):
        self.calls = []                                    # 按发生顺序保存动作
        FakeBrowser.last = self

    def open(self, url, **kwargs):
        self.calls.append(("open", url))
        return True

    def click(self, selector, **kwargs):
        self.calls.append(("click", selector))
        return True

    def fill(self, selector, value, **kwargs):
        self.calls.append(("fill", selector, value))
        return True

    def type(self, selector, value, **kwargs):
        self.calls.append(("type", selector, value))
        return True

    def fill_form(self, fields, **kwargs):
        self.calls.append(("fill_form", fields))
        return True

    def visible(self, selector, timeout=3):
        return selector == SELECTORS["code"]               # 邮箱提交后验证码框立即出现

    def exists(self, selector, timeout=3):
        return True                                        # 离线流程模拟页面存在 Turnstile

    def text(self):
        if ("click", SELECTORS["submit"]) in self.calls:
            return "管理您的帐户"                           # 最终提交后进入账户中心
        return ""                                          # 页面没有拒绝提示

    def sleep(self, seconds):
        return self                                        # 假等待不真的拖慢测试

    def press(self, selector, key):
        self.calls.append(("press", selector, key))
        return True

    def run_js(self, script):
        if "cf-turnstile-response" in script:
            return "passed"                                # 离线流程模拟真人验证已完成
        self.calls.append(("run_js", script))
        return None

    def cookie(self, name):
        return "sso-token" if name == "sso" else ""        # 最终成功凭证

    def shot(self, path):
        self.calls.append(("shot", path))
        return path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class MailList:
    """按顺序返回多个邮箱，用于验证黑名单重选。"""

    def __init__(self):
        self.items = iter(["bad@kkb.qzz.io", "good@mail.test"])

    def create(self, **kwargs):
        return next(self.items)                            # 第一次黑名单，第二次合法


def test_register_flow_offline(tmp_path):
    output = tmp_path / "tokens.txt"                       # 使用临时结果文件
    settings = Settings(output=str(output), page_timeout=1, mail_timeout=1)
    account = register(
        settings,
        browser_type=FakeBrowser,
        mail_type=FakeMail,
        person_type=FakePerson,
        line_type=Lines,
    )

    assert account["email"] == "demo@mail.test"            # 邮箱来自 Mail
    assert account["token"] == "sso-token"                 # token 来自浏览器 Cookie
    assert Lines(output).read() == ["sso-token"]           # token 已用新保存器落盘
    assert ("type", SELECTORS["code"], "ABC-123") in FakeBrowser.last.calls  # 验证码被逐字输入
    assert not any(call[0] == "shot" for call in FakeBrowser.last.calls)  # 成功流程不会产生失败截图


def test_make_email_rejects_blacklist():
    assert make_email(MailList()) == "good@mail.test"      # 黑名单地址被丢弃并重新申请


def test_cli_is_non_interactive():
    args = parse_args(["--count", "2", "--headless", "--flow-timeout", "45", "--check"])
    settings = settings_from(args)                         # 参数直接转换，无 input() 阻塞
    assert settings.count == 2
    assert settings.headless is True
    assert settings.flow_timeout == 45
    assert settings.check is True


def test_real_register_rejects_headless():
    assert main(["--headless"]) == 2                        # 正式注册必须有头，且应在启动浏览器前拒绝


def test_block_page_has_clear_error():
    class BlockPage:
        def text(self):
            return "Sorry, you have been blocked. You are unable to access x.ai"

    with pytest.raises(RuntimeError, match="更换代理"):
        reject_blocked(BlockPage())                        # 封禁应诊断为网络节点问题，不误报选择器错误


def test_blocked_page_does_not_create_mail(tmp_path):
    class BlockBrowser(FakeBrowser):
        def text(self):
            return "Sorry, you have been blocked"          # 打开页面后立即识别封禁

    class CountMail(FakeMail):
        made = 0

        def __init__(self, **kwargs):
            CountMail.made += 1                            # 只要创建邮箱对象就计数
            super().__init__(**kwargs)

    settings = Settings(output=str(tmp_path / "tokens.txt"))
    with pytest.raises(RuntimeError, match="更换代理"):
        register(settings, browser_type=BlockBrowser, mail_type=CountMail, person_type=FakePerson)
    assert CountMail.made == 0                              # 网络不可访问时不应浪费临时邮箱资源


def test_challenge_without_widget_passes():
    class PlainPage:
        def exists(self, selector, timeout=3):
            return False                                   # 页面没有 Turnstile

    assert wait_challenge(PlainPage(), 1) is True           # 无挑战不应产生额外等待


def test_final_submit_email_rejection_stays_on_page():
    class RejectedPage:
        def cookie(self, name):
            return ""

        def text(self):
            return "该邮箱地址已被拒绝"

    with pytest.raises(EmailRejected):
        wait_registration(RejectedPage(), 1)                # 不应导航离开并掩盖最终拒绝提示


def test_existing_account_email_is_retried():
    class ExistingPage:
        def cookie(self, name):
            return ""

        def text(self):
            return "找到现有帐户 已存在与此邮箱地址关联的帐户"

    with pytest.raises(EmailRejected):
        wait_registration(ExistingPage(), 1)                # 公共邮箱已注册时应换新邮箱


def test_mail_timeout_restarts_flow(monkeypatch):
    calls = []

    def try_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise EmailUnavailable("mail timeout")
        return {"token": "ok"}

    monkeypatch.setattr("register.grok.register_once", try_once)
    assert register(Settings(email_attempts=2))["token"] == "ok"
    assert len(calls) == 2                                  # 收不到验证码时也应换渠道重开


def test_email_rejection_restarts_flow(monkeypatch):
    calls = []                                              # 记录每次完整尝试

    def try_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise EmailRejected("bad domain")              # 第一次域名被拒绝
        return {"token": "ok"}                             # 第二次换邮箱成功

    monkeypatch.setattr("register.grok.register_once", try_once)
    result = register(Settings(email_attempts=2))           # 允许两次完整尝试
    assert result["token"] == "ok"
    assert len(calls) == 2                                  # 应关闭旧流程并完整重开一次
