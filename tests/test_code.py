"""验证码与链接提取的测试：确认各种格式的验证码、链接都能被正确挖出来。"""

from tools.code import find_code, find_link, strip_tags     # 被测的三个提取函数


# --- 中文验证码场景 ---
def test_find_code_chinese():
    text = "您的验证码是 483920，5分钟内有效"                # 典型中文验证码邮件
    assert find_code(text) == "483920"                     # 应精确挖出那 6 位数字


# --- 英文验证码场景 ---
def test_find_code_english():
    text = "Your verification code is 582013. It expires soon."  # 典型英文验证码邮件
    assert find_code(text) == "582013"                     # 应挖出验证码


# --- 分段码场景（如 x.ai 的 A9F-3KD） ---
def test_find_code_segmented():
    text = "Enter this code to continue: A9F-3KD"           # 带横杠的分段验证码
    assert find_code(text) == "A9F-3KD"                    # 应完整挖出含横杠的码


# --- 裸 6 位数字场景 ---
def test_find_code_plain_six():
    text = "hello 123456 world"                            # 正文里就一串 6 位数字
    assert find_code(text) == "123456"                     # 兜底规则应能挖出


# --- 没有验证码时应返回空串 ---
def test_find_code_none():
    assert find_code("no code here at all") == ""          # 找不到码返回空串，不报错
    assert find_code("") == ""                             # 空输入也安全返回空串


# --- 从 HTML 里挖验证码 ---
def test_find_code_in_html():
    html = "<p>Code: <b>778899</b></p>"                    # 验证码被包在 HTML 标签里
    assert find_code(html) == "778899"                     # 去标签后应能挖出


# --- 从视觉突出节点里优先找验证码，而不是页脚年份 ---
def test_find_code_prefers_html_code_node():
    html = '<footer>2026</footer><div class="otp" style="font-size:26px">884422</div>'
    assert find_code(html) == "884422"                      # otp 大号节点应比普通年份更可信


# --- 找第一条链接 ---
def test_find_link_first():
    text = "Click https://example.com/go to proceed"        # 正文含一条链接
    assert find_link(text) == "https://example.com/go"     # 不带关键词时返回第一条


# --- 按关键词挑链接 ---
def test_find_link_keyword():
    text = "home https://a.com/home verify https://a.com/verify?t=1"  # 正文含多条链接
    assert find_link(text, keyword="verify") == "https://a.com/verify?t=1"  # 应挑出含 verify 的那条


# --- HTML 链接里的 &amp; 应还原成真正参数连接符 ---
def test_find_link_unescapes_html():
    html = '<a href="https://a.com/verify?a=1&amp;b=2">Verify</a>'
    assert find_link(html) == "https://a.com/verify?a=1&b=2"  # 返回可直接请求的真实链接


# --- 没有链接时返回空串 ---
def test_find_link_none():
    assert find_link("plain text no url") == ""            # 无链接安全返回空串


# --- 去 HTML 标签 ---
def test_strip_tags():
    html = "<div>Hello <b>World</b></div>"                 # 带标签的 HTML 片段
    assert strip_tags(html) == "Hello World"               # 应还原成纯文字且单词不粘连
