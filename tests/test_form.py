"""HTML 表单解析测试：验证纯 HTTP 复现表单时需要的静态协议信息。"""

from lazyfox import first_form, result, turnstile            # 顶层公开入口应能直接导入表单工具


html = """
<html><head><title>Demo</title></head><body>
  <form method="post" action="/submit">
    <input name="team" maxlength="40" required value="fox">
    <textarea name="message" maxlength="180" required>Hello</textarea>
    <div class="cf-turnstile" data-sitekey="site" data-theme="dark"></div>
  </form>
  <p class="eyebrow">Access Denied</p>
  <h1>Closed</h1>
  <p class="lead">invalid-input-response</p>
</body></html>
"""


def test_form_contract_and_turnstile_config():
    form = first_form(html)

    assert form.method == "POST"
    assert form.action == "/submit"
    assert form.field("team").maxlength == 40
    assert form.data(team="lazyfox")["team"] == "lazyfox"
    assert turnstile(html)["sitekey"] == "site"


def test_result_summary():
    feedback = result(html)

    assert feedback.title == "Demo"
    assert feedback.label == "Access Denied"
    assert "invalid-input-response" in feedback.summary()
