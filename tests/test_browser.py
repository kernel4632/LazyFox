"""浏览器工具的测试：用本地 HTML 文件验证 open/fill/click/智能判断 的真实行为。

说明：这个测试会真的拉起一个无头 Chrome，比纯逻辑测试慢几秒；若本机没有可用的
Chrome，会自动跳过而不是判失败，避免在没有浏览器的环境里误报。
"""

import pytest                                               # 测试框架，用它的 skip 在无浏览器时跳过
from tools.browser import Browser, clean_args               # 被测的浏览器工具和 Chrome 基础参数


# 一个自带按钮和成功面板的最小页面：点按钮后面板才显示，用来验证"点击成功判断"
demo_html = """
<!doctype html><html><body>
<h1 id="title">Login Demo</h1>
<input id="user" />
<input id="agree" type="checkbox" />
<select id="color"><option value="red">Red</option><option value="blue">Blue</option></select>
<button id="go" onclick="window.clicks=(window.clicks||0)+1;document.getElementById('panel').style.display='block'">Submit</button>
<div id="panel" style="display:none">Welcome Back</div>
</body></html>
"""


# --- 准备一个本地测试页面并返回它的 file:// 网址 ---
@pytest.fixture
def page_url(tmp_path):
    # tmp_path 是 pytest 的临时目录，测试结束自动清理
    file = tmp_path / "demo.html"                          # 临时页面文件
    file.write_text(demo_html, encoding="utf-8")           # 把测试页写进去
    return file.as_uri()                                   # 转成 file:// 网址供浏览器打开


# --- 端到端验证：打开、填写、智能点击、读取 ---
def test_browser_flow(page_url):
    try:
        browser = Browser(headless=True)                   # 无头启动，测试环境不弹窗
    except Exception as error:                             # 本机没有 Chrome 或启动失败
        pytest.skip(f"无可用浏览器，跳过：{error}")          # 跳过而非失败，保持测试套件在任何机器可跑

    with browser as page:                                  # 用 with 确保结束后自动关闭浏览器
        assert page.open(page_url, appear="#title")        # 打开页面且标题元素出现才算成功
        assert page.text("#title") == "Login Demo"         # 读取标题文字应准确

        assert page.fill("#user", "alice", verify=True)    # 填写并回读验证真实 value
        assert page.value("#user") == "alice"              # value() 应读到实时输入值
        assert page.type("#user", "bob", delay=0, value_is="bob")  # 逐字输入也支持精确回读
        assert page.check_box("#agree")                    # 勾选复选框
        assert page.select("#color", "blue")               # 选择下拉项并触发 change 事件
        assert page.locate("button") is not None            # 无前缀纯标签应先按 CSS 正确定位
        assert page.locate("text=Submit") is not None       # 显式文字前缀应正确定位
        assert page.locate(("#missing", "text=Submit")) is not None  # 候选列表应命中第二个选择器
        assert page.exists("#go")                          # 提交按钮应存在
        assert page.exists("#panel")                       # 隐藏面板已经挂在 DOM
        assert page.visible("#panel") is False             # 但出现条件不能把 display:none 误判为可见

        # 核心：点击并要求成功面板出现才算成功，验证"智能判断"生效
        assert page.click("#go", appear="#panel")
        assert "Welcome" in page.text("#panel")            # 面板文字应正确
        clicks = page.run_js("window.clicks")               # 记录成功提交次数
        assert page.click("#go", appear="#panel", skip_if_done=True)  # 恢复模式下目标已成立应直接成功
        assert page.run_js("window.clicks") == clicks       # 不应再点一次提交按钮

        # 反向验证：期望不可能出现时返回 False，但副作用按钮默认只点击一次，不随 tries 重复提交
        before = page.run_js("window.clicks")               # 记录此前成功点击次数
        assert page.click("#go", appear="#ghost", tries=3, gap=0.1) is False
        assert page.run_js("window.clicks") == before + 1   # 三轮判断只真正点击一次


def test_background_window_throttling_is_disabled():
    assert "--disable-backgrounding-occluded-windows" in clean_args
    assert "--disable-features=CalculateNativeWinOcclusion" in clean_args
