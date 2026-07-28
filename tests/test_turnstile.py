"""Turnstile 审计测试：只识别官方配置，不生成真实验证 token。"""

from lazyfox import turnstile_attach, turnstile_audit, turnstile_dummy, turnstile_verify


class FakeHTTP:
    """模拟 Cloudflare siteverify，避免测试访问真实网络。"""

    def __init__(self, payload):
        self.payload = payload
        self.sent = None

    def post(self, url, **kwargs):
        self.sent = (url, kwargs)

        class Response:
            text = "{}"

            def json(inner_self):
                return self.payload

        return Response()


def test_turnstile_test_key_detection():
    html = '<div class="cf-turnstile" data-sitekey="1x00000000000000000000AA" data-theme="dark"></div>'
    item = turnstile_audit(html)

    assert item.testing() is True
    assert item.dummy_allowed() is True
    assert item.behavior == "always-pass-visible"


def test_turnstile_production_key_detection_and_attach():
    html = '<div class="cf-turnstile" data-sitekey="0x4AAAAAADw2q5H9gJ3lugym"></div>'
    item = turnstile_audit(html)

    assert item.mode == "production"
    assert item.dummy_allowed() is False
    assert turnstile_attach({"team": "fox"}, "token")["cf-turnstile-response"] == "token"
    assert turnstile_dummy({})["cf-turnstile-response"] == "XXXX.DUMMY.TOKEN.XXXX"


def test_turnstile_verify_success_result():
    web = FakeHTTP({"success": True, "hostname": "example.com", "action": "test"})
    result = turnstile_verify("token", "secret", remoteip="127.0.0.1", web=web)

    assert result.success is True
    assert result.hostname == "example.com"
    assert web.sent[1]["data"]["remoteip"] == "127.0.0.1"
    assert "secret" in web.sent[1]["data"]


def test_turnstile_verify_failure_result():
    result = turnstile_verify("bad", "secret", web=FakeHTTP({"success": False, "error-codes": ["invalid-input-response"]}))

    assert result.success is False
    assert result.errors == ["invalid-input-response"]
    assert "invalid-input-response" in result.summary()
