"""协议探针测试：验证请求变体生成和响应聚类的离线行为。"""

from types import SimpleNamespace                           # 构造最小响应对象，不需要真实网络

from lazyfox import form_cases, groups, run_probes          # 顶层 API 应直接暴露探针工具


class FakeHTTP:
    """按请求参数返回固定 HTML，用来验证探针不依赖真实站点。"""

    def request(self, method, path, **kwargs):
        token = ""
        if kwargs.get("data"):
            token = kwargs["data"].get("cf-turnstile-response", "")
        status = 403 if token else 400
        body = f"<title>Result</title><p class='lead'>{status}</p>"
        return SimpleNamespace(status_code=status, url=f"https://example.test{path}", text=body)


def test_form_probe_cases_and_groups():
    cases = form_cases("/submit", {"team": "fox", "message": "probe"})
    findings = run_probes(FakeHTTP(), cases)

    assert any(case.name == "duplicate-token" for case in cases)
    assert {item.status for item in findings} == {400, 403}
    assert len(groups(findings)) == 2                       # 缺 token 和假 token 应聚成两类分支


def test_groups_ignore_body_hash_noise():
    left = SimpleNamespace(status_code=400, url="https://e.test", text="<title>T</title><p class='lead'>same</p>a")
    right = SimpleNamespace(status_code=400, url="https://e.test", text="<title>T</title><p class='lead'>same</p>b")

    findings = run_probes(FakeHTTP(), [])                   # 复用公开入口，空实验应返回空列表
    assert findings == []
    from tools.probe import _finding

    assert len(groups([_finding("a", left), _finding("b", right)])) == 1
