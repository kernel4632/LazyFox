"""Turnstile 审计测试：只识别官方配置，不生成真实验证 token。"""

from lazyfox import turnstile_attach, turnstile_audit, turnstile_dummy


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
