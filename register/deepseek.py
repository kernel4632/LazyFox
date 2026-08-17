"""
DeepSeek 注册示例：展示怎样用 LazyFox 的 Browser、Mail、Person、Lines 和 Table 编写完整注册流程。

这个文件只保留 DeepSeek 特有内容：注册地址、页面选择器、手机号分流判断和 localStorage token 读取。
浏览器重试、输入校验、临时邮箱轮询、验证码提取、身份生成、日志和结果保存全部交给 LazyFox。
被页面拒绝的邮箱域名会记录到 accounts/deepseek-bad-domains.txt，供后续排除。命令行没有 input()。

运行示例：
    uv run python register/deepseek.py --count 1
    uv run python register/deepseek.py --count 3 --flow-timeout 300
    uv run python register/deepseek.py --forever --flow-timeout 300   # 无限循环筛选不可用邮箱域名
    uv run python register/deepseek.py --check --headless --flow-timeout 45

安全边界：
每个账号在独立子进程里运行，超过 flow_timeout 后终止整棵进程树，防止浏览器或网络请求
一直挂住终端。--check 只打开注册页并检查入口，不填写或提交任何账号信息。
"""

import argparse                                             # 非交互命令行参数，替代 console.input
import json                                                 # 解析 localStorage 快照并构造 JS 键名
import multiprocessing                                     # 每个账号放进独立进程，提供硬超时边界
import os                                                   # 判断系统并在 Windows 上终止完整浏览器进程树
import queue                                                # 安全读取子进程结果，区分超时和无返回
import subprocess                                           # Windows taskkill 用于清理子进程及 Chrome 后代
import time                                                 # 页面阶段轮询和批次间隔
from dataclasses import dataclass                           # 集中表达一次注册的可控参数
from pathlib import Path                                   # 生成失败截图路径

from lazyfox import Browser, Lines, Log, Mail, Person, Table, find_code, strip_tags  # 脚手架公开入口：浏览器、邮箱、身份、保存、日志和验证码提取


SIGN_UP_URL = "https://chat.deepseek.com/sign_up"          # DeepSeek 注册入口
BAD_DOMAINS = "accounts/deepseek-bad-domains.txt"          # 被页面拒绝的邮箱域名记录文件，accounts/ 已被 gitignore
BLACKLIST = {
    "email.tsdpt.co.uk",                                   # 实测被 DeepSeek 拒收的临时邮箱后缀
}                                                          # 已知被 DeepSeek 拒绝的邮箱域名，发现后加进这里


# --- 启动时把历史拒绝域名合并进内存黑名单 ---
def load_blacklist():
    # 文件里保存的是之前筛选出的不可用域名，合并后 make_email 就不会再申请这些域名。
    for domain in Lines(BAD_DOMAINS).read():               # 文件不存在时 Lines.read 返回空列表
        BLACKLIST.add(domain.lower())                      # 统一小写，和 is_blacklisted 判断保持一致
    return len(BLACKLIST)                                  # 返回当前黑名单数量，方便启动时打日志


load_blacklist()                                           # 模块导入时执行一次；子进程 spawn 也会重新执行一次
EMAIL_REJECTED = (
    "邮箱已被注册", "邮箱已注册", "该邮箱已被注册", "email already registered",
    "已被使用", "already in use", "邮箱格式不正确", "invalid email",
    "邮箱无效", "邮箱不可用", "请输入有效邮箱",
    "域名不支持", "不支持的邮箱", "暂不支持该邮箱",
    "该邮箱不支持", "邮箱不支持", "不支持当前邮箱", "该邮箱域名不支持",
    "邮箱地址无效", "邮箱地址格式", "邮箱地址错误", "邮箱格式错误",
    "unsupported email", "invalid email address", "email not supported",
    "email is not supported", "please enter a valid email", "email address is not",
)
# 页面同时出现“邮箱/email”和这些“强拒绝词”时才判定当前邮箱不可用。
# 只保留真正表示拒绝的词，去掉“更换、重新输入、错误”等正常页面也会出现的词，避免误判。
EMAIL_BAD_WORDS = (
    "不支持", "暂不支持", "无效", "不可用", "无法使用",
    "not support", "unsupported", "invalid", "not allowed", "rejected", "unavailable",
)
ENV_BLOCKED = ("运行环境异常", "更换环境", "设备环境异常")  # 页面出现这些文字时说明当前节点被风控，停止注册
TOKEN_KEYS = ("userToken", "USER_TOKEN", "token", "access_token", "authToken")  # localStorage 常见登录键名

# 页面选择器是本示例唯一需要跟随 DeepSeek 页面变化维护的数据。
# 每个字段按“语义选择器优先、旧页面兜底”的顺序排列，浏览器会依次尝试直到命中。
SELECTORS = {
    "email": (
        "css=input[placeholder='请输入邮箱']",              # 实测中文 placeholder，最可靠
        "css=input[type='email']",                         # 语义属性兜底
        "css=input[placeholder='Email address']",          # 英文页面
        "css=div.ds-form-item:nth-child(2) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[2]/div[1]/div/input",
    ),
    "password": (
        "css=input[placeholder='请输入密码']",              # 实测中文 placeholder
        "css=div.ds-form-item:nth-child(3) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[3]/div[1]/div/input",
    ),
    "password_again": (
        "css=input[placeholder='请再次输入密码']",          # 实测中文 placeholder
        "css=div.ds-form-item:nth-child(4) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[4]/div[1]/div/input",
    ),
    "send_code": (
        "css=.ds-verify-code-input-countdown",              # 实测发送按钮的独特 class，最可靠
        "text=发送验证码",                                   # 中文页面优先
        "text=Send code",                                   # 英文页面
        "text=获取验证码",                                   # 部分版本用这个词
        "text=Get code",                                    # 部分英文版本用这个词
        "css=button.ds-link-button:nth-child(2) > span:nth-child(1)",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[5]/div[1]/div/div/button/span",
    ),
    "code": (
        "css=input[placeholder='请输入验证码']",            # 实测中文 placeholder，最可靠
        "css=div.ds-form-item:nth-child(5) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
        "css=input[placeholder='验证码']",
        "css=input[placeholder='Verification code']",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[5]/div[1]/div/input",
    ),
    "submit": (
        "css=div.ds-button--filled",                        # 实测“注册”按钮的独特 class
        "text=注册",                                        # 中文页面优先
        "text=Sign Up",                                     # 英文页面
        "css=button.ds-basic-button--primary",
        "css=.ds-atom-button",
        "xpath=/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/button",
    ),
}

log = Log("deepseek-register")                             # 全流程共用同名彩色日志
_suspicious_logged = False                                 # 疑似邮箱拒绝提示只打印一次，避免等待循环刷屏


class EmailUnavailable(RuntimeError):
    """当前邮箱无法完成注册，可以更换邮箱后安全重试。"""


class EmailRejected(EmailUnavailable):
    """DeepSeek 明确拒绝临时邮箱域名或邮箱已关联其他账户。"""


@dataclass
class Settings:
    """一次 DeepSeek 注册及其外层硬超时配置。"""

    count: int = 1                                         # 批量账号数量
    headless: bool = False                                 # 是否隐藏浏览器窗口
    proxy: str | None = None                               # 浏览器和临时邮箱共用代理
    mail_channel: str | None = "emailnator"                # 默认 emailnator 提供 gmail 邮箱，满足 DeepSeek 域名要求
    mail_timeout: int = 120                                # 等验证码最长秒数
    email_attempts: int = 3                                # 域名被拒绝时最多换邮箱重开流程次数
    page_timeout: int = 30                                 # 每个页面阶段最长秒数
    challenge_timeout: int = 120                           # 等待用户在有头浏览器完成真人验证的秒数
    flow_timeout: int = 300                                # 单账号子进程硬存活上限
    gap: float = 3.0                                       # 两个账号之间等待秒数
    output: str = "token/deepseek-tokens.txt"              # token 保存位置，token/ 已被 gitignore
    accounts: str = "accounts/deepseek-accounts.json"      # 完整账号保存位置，accounts/ 已被 gitignore
    forever: bool = False                                  # 无限循环筛选，忽略 count，直到手动 Ctrl+C 停止
    check: bool = False                                    # 只检查注册入口，不产生注册行为


# --- 申请一个不在黑名单里的临时邮箱 ---
def make_email(mail, attempts=5):
    for attempt in range(1, attempts + 1):                 # 服务域名随机时允许有限次重选
        address = mail.create(tries=2, total_timeout=45)    # SDK 负责渠道请求重试和总超时
        domain = address.rsplit("@", 1)[-1].lower()         # 只取域名用于黑名单判断
        if not is_blacklisted(domain):
            log.info(f"临时邮箱：{address}")               # 合法地址立即交给注册流程
            return address
        log.warning(f"邮箱域名被列入黑名单，第 {attempt}/{attempts} 次重新申请：{domain}")
    raise RuntimeError("连续申请到被拒绝的临时邮箱域名")     # 有限重试后明确失败，不无限循环


# --- 判断邮箱域名是否在黑名单里，支持子域名后缀匹配 ---
def is_blacklisted(domain):
    # 黑名单里写主域名时，也要拦住它的子域名，比如 tsdpt.co.uk 和 email.tsdpt.co.uk 一起拦截。
    return any(domain == item or domain.endswith("." + item) for item in BLACKLIST)


# --- 把被拒绝的邮箱域名记录到文件，自动去重 ---
def record_bad_domain(domain):
    domain = (domain or "").strip().lower()                # 统一小写并去空白，避免同一域名大小写不同重复记录
    if not domain:
        return False                                       # 空域名没有记录意义
    saved = Lines(BAD_DOMAINS).add(domain)                 # Lines 自带原子写入和去重
    if saved:
        log.warning(f"发现不可用邮箱域名：{domain}，已记录到 {BAD_DOMAINS}")
    return saved                                           # 返回是否真的新增，方便外层统计

# --- 轮询等待验证码，并把每轮状态实时打到终端 ---
def wait_code(mail, timeout, interval=3, page=None):
    log.info(f"开始等待邮箱验证码，最长 {timeout} 秒，每 {interval} 秒检查一次")
    deadline = time.monotonic() + timeout                  # 整个等待过程共用一个截止时间
    while time.monotonic() < deadline:
        if page:
            reject_env_error(page)                          # 等待邮件期间页面也可能弹出环境异常提示
            reject_email(page)                              # 等待邮件期间页面也可能弹出邮箱不支持提示
        remaining = deadline - time.monotonic()
        item = mail.wait_mail(timeout=min(interval, remaining), interval=interval)
        if not item:
            log.info(f"邮箱暂时没有新邮件，剩余 {int(remaining)} 秒，继续等待")
            continue

        subject = item.subject or ""                        # 新邮件主题，用于展示和拼进提取文本
        body = item.text or strip_tags(item.html or "")     # 优先纯文本正文，没有就把 HTML 洗成文字
        code = find_code(subject + "\n" + body)             # 交给提取工具按常见格式挖验证码
        log.info(f"收到新邮件，主题：{subject}")
        if code:
            log.info(f"已提取验证码：{code}")
            return code
        log.warning("这封邮件里没有验证码，继续等待下一封")
    log.error(f"等待验证码超时，最新邮箱错误：{mail.error or '无'}")
    return ""                                               # 超时返回空串，交给调用方走失败分支

# --- 检查页面是否明确拒绝邮箱 ---
def reject_email(page):
    global _suspicious_logged                              # 标记跨多次调用共享，避免重复打印

    page_text = page.text().lower()                         # 不依赖易变的错误提示元素选择器
    if any(marker in page_text for marker in EMAIL_REJECTED):
        log.error(f"页面明确提示邮箱不可用，整页文本如下：\n{page.text()[:600]}")
        raise EmailRejected("DeepSeek 拒绝了当前临时邮箱")

    # 保守检测：只“怀疑”是拒绝提示时，打印整页文本但不停止流程，方便确认真实文案。
    has_mail_word = any(word in page_text for word in ("邮箱", "email", "mail"))
    has_bad_word = any(word in page_text for word in EMAIL_BAD_WORDS)
    if has_mail_word and has_bad_word and not _suspicious_logged:
        _suspicious_logged = True
        log.warning(f"检测到疑似邮箱拒绝提示，先不停止流程，整页文本如下：\n{page.text()[:600]}")
    return False
    return False


# --- 填写邮箱后确认页面接受了这个邮箱，没出现格式或域名错误 ---
def ensure_email_accepted(page, timeout=6):
    # 页面校验是异步的，填写后要等一小会，让“邮箱格式不正确/不支持该域名”这类提示有机会出现。
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reject_env_error(page)                              # 等邮箱校验时也可能弹出环境异常
        reject_email(page)                                  # 出现邮箱错误立即抛出，交给外层换邮箱重试
        if page.visible(SELECTORS["send_code"], timeout=1):  # 发送验证码按钮出现说明邮箱已通过页面校验
            return True
        page.sleep(0.5)
    return True                                             # 超时不误判，交给后续 send_code 继续处理


# --- 检查页面是否出现设备运行环境异常提示 ---
def reject_env_error(page):
    page_text = page.text().lower()                         # 页面任意位置出现风控提示都要立即停止
    if any(marker in page_text for marker in ENV_BLOCKED):
        raise RuntimeError("页面提示设备运行环境异常，请更换环境后重试")
    return False


# --- 检查页面是否被分流到手机号注册模式 ---
def reject_phone_mode(page):
    # 部分节点访问注册页时只会出现手机号输入框，临时邮箱流程无法继续，应更换代理后重试。
    phone_markers = (
        "css=input[placeholder='Phone number']",
        "css=input[placeholder='手机号']",
        "text=手机号",
    )
    if page.exists(phone_markers, timeout=2):
        raise RuntimeError("当前节点被分流到手机号注册模式，请更换代理后重试")


# --- 从 localStorage 或 Cookie 中读取登录 token ---
def read_token(page):
    # DeepSeek 网页把登录凭证存在 localStorage，不同版本键名略有差异，逐个尝试。
    for key in TOKEN_KEYS:
        token = page.run_js(f"localStorage.getItem({json.dumps(key)})") or ""
        if "." in token and len(token) > 20:               # 登录凭证都是带点号的长字符串（JWT）
            return token

    # 兜底：扫描整个 localStorage，找出长得像 JWT 的长字符串，避免漏掉未知键名。
    raw = page.run_js("JSON.stringify(localStorage)") or "{}"
    try:
        values = json.loads(raw)                           # localStorage 快照是 JSON 字符串，转回字典
    except Exception:
        values = {}
    if isinstance(values, dict):
        for value in values.values():
            if isinstance(value, str) and "." in value and len(value) > 20:
                return value

    # 最后尝试 Cookie，某些旧版本会写入名为 token 的 Cookie。
    return page.cookie("userToken") or page.cookie("token")


# --- 提交注册并在原页面等待登录 token 写入 ---
def submit_registration(page, timeout, retry_gap=3):
    deadline = time.monotonic() + timeout
    next_click = 0                                          # 慢页面吞掉点击时按固定间隔补点，不连续提交
    while time.monotonic() < deadline:
        token = read_token(page)
        if token:
            return token
        reject_email(page)                                  # 最终提交仍可能拒绝临时邮箱
        reject_env_error(page)                              # 提交后页面可能直接出现环境异常
        now = time.monotonic()
        if now >= next_click and page.visible(SELECTORS["submit"], timeout=1):
            page.click(SELECTORS["submit"], tries=1)        # 按钮仍在原页才补点，离开表单后不会重复注册
            next_click = now + retry_gap
        page.sleep(0.5)
    return ""                                               # 超时交给调用方生成失败截图


# --- 等待有头浏览器中的 Cloudflare 真人验证 ---
def wait_challenge(page, timeout):
    # 没有 Turnstile 时直接继续；出现挑战时只等待用户手动完成，不尝试绕过。
    challenge = (
        "css=input[name='cf-turnstile-response']",
        "css=iframe[src*='turnstile']",
    )
    if not page.exists(challenge, timeout=2):              # 当前页面没有挑战控件
        return True

    if hasattr(page, "front"):
        page.front()                                        # 只在真人步骤恢复窗口并播放提示音
    log.warning(f"请在浏览器中完成人机验证，最多等待 {timeout} 秒")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reject_env_error(page)                              # 验证过程中页面若切换成风控提示要立即停止
        reject_email(page)                                  # 验证过程中页面也可能弹出邮箱不支持提示
        script = "document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''"
        if page.run_js(script):                             # Turnstile 完成后会写入隐藏响应字段
            log.info("人机验证已完成")
            return True
        page.sleep(0.5)                                     # 有限频率检查，不占满 CPU
    return False


# --- 判断当前页面是否真的出现了 Turnstile 人机验证控件 ---
def has_challenge(page):
    # 用 exists 判断 DOM 是否出现，因为 cf-turnstile-response 输入框在验证前后都可能存在于 DOM。
    challenge = (
        "css=input[name='cf-turnstile-response']",
        "css=iframe[src*='turnstile']",
    )
    return page.exists(challenge, timeout=1)


# --- 用 JS 扫描并点击文案含“验证码/code”的按钮，绕开脆弱的选择器 ---
def click_send_code(page):
    # 依次尝试“发送验证码、获取验证码、发送、Send code、Get code”等常见文案，命中就点击。
    script = """
    (() => {
      const words = ["发送验证码", "获取验证码", "发送", "Send code", "Get code", "Send"];
      const nodes = [...document.querySelectorAll("div, button, a, [role=button], span")];
      for (const node of nodes) {
        const text = (node.innerText || node.textContent || "").trim();
        if (!text) continue;
        const hit = words.some((w) => text === w || text.includes(w));
        if (!hit) continue;
        if (node.offsetParent === null) continue;          // 只点页面真正可见的元素
        node.click();
        return text;
      }
      return "";
    })();
    """
    return page.run_js(script) or ""                       # 返回命中的按钮文案，空串表示没找到


# --- 读取发送验证码按钮当前文字，用于判断是否已进入倒计时 ---
def send_button_text(page):
    script = """
    (() => {
      const nodes = [...document.querySelectorAll(".ds-verify-code-input-countdown")];
      for (const node of nodes) {
        if (node.offsetParent === null) continue;          // 只取真正可见的按钮
        return (node.innerText || node.textContent || "").trim();
      }
      return "";
    })();
    """
    return page.run_js(script) or ""                       # 返回按钮当前文字，空串表示没找到


# --- 判断按钮文字是否已从“发送验证码”变成倒计时或重新发送 ---
def code_sent(button_text):
    text = (button_text or "").strip()
    if not text or "发送验证码" in text or "获取验证码" in text or "Send code" in text:
        return False                                       # 还是可点击状态，说明还没成功发出去
    # 出现“重新发送”“Resend”或倒计时数字，都说明已经发出验证码。
    return any(
        marker in text
        for marker in ("重新发送", "重新获取", "Resend", "resend", "秒", "s")
    )


# --- 点击发送验证码按钮，直到按钮进入倒计时才算发送成功 ---
def send_code(page, timeout, challenge_timeout=120):
    deadline = time.monotonic() + timeout                  # 整个发送步骤共享一个超时
    clicked = False                                        # 记录是否已经点过按钮，避免每轮重复点击
    while time.monotonic() < deadline:
        reject_email(page)                                  # 发送时也可能立即提示邮箱不可用
        reject_env_error(page)                              # 发送验证码前后都可能出现环境异常

        # 按钮文字从“发送验证码”变成倒计时，说明网站已确认发送成功。
        if code_sent(send_button_text(page)):
            log.info("验证码已发送，按钮进入倒计时")
            return True

        # 页面弹出人机验证时，先把浏览器让给用户手动完成，再回来继续检测。
        if page.exists((
            "css=input[name='cf-turnstile-response']",
            "css=iframe[src*='turnstile']",
        ), timeout=1):
            if not wait_challenge(page, challenge_timeout):
                return False                               # 人机验证超时，无法继续
            continue                                       # 验证完成后回到循环，看按钮是否进入倒计时

        # 只要还没点过按钮，就主动点击一次；选择器失败时用 JS 扫描兜底。
        if not clicked:
            if page.click(SELECTORS["send_code"], tries=1):
                clicked = True
                log.info("已点击发送验证码按钮（选择器命中）")
            else:
                text = click_send_code(page)               # JS 扫描文案兜底
                if text:
                    clicked = True
                    log.info(f"已点击发送验证码按钮（JS 兜底命中：{text}）")

        page.sleep(0.5)                                     # 控制检测频率，避免空转占满 CPU
    return False                                           # 超时按钮仍未进入倒计时，交给调用方失败


# --- 执行一个完整注册流程 ---
def register(settings, browser_type=Browser, mail_type=Mail, person_type=Person, line_type=Lines, table_type=Table):
    for attempt in range(1, settings.email_attempts + 1):  # 所有尝试仍受外层 flow_timeout 硬限制
        try:
            return register_once(settings, browser_type, mail_type, person_type, line_type, table_type)
        except EmailUnavailable:
            if attempt >= settings.email_attempts:
                raise                                      # 用尽次数后保留明确错误
            log.warning(f"邮箱域名被拒绝，准备更换邮箱重试 {attempt + 1}/{settings.email_attempts}")
    raise RuntimeError("邮箱重试流程异常结束")              # 理论兜底


# --- 使用一个新浏览器和新邮箱尝试注册一次 ---
def register_once(settings, browser_type=Browser, mail_type=Mail, person_type=Person, line_type=Lines, table_type=Table):
    # 可替换的 type 参数让流程可以离线测试，真实运行保持零额外配置。
    person = person_type(lang="en").all()                  # 一次生成关联一致的姓名和密码
    channel = None if settings.mail_channel == "random" else settings.mail_channel

    browser_args = [
        "--disable-ipc-flooding-protection",                # 长时间页面等待时避免 IPC 限流
        "--disable-hang-monitor",                           # Chrome 不弹出页面无响应提示
        "--disable-features=PasswordManager,PasswordManagerOnboarding,PasswordLeakDetection",
    ]

    with browser_type(
        headless=settings.headless,
        proxy=settings.proxy,
        args=browser_args,
    ) as page:
        try:
            ready = page.open(SIGN_UP_URL, appear=SELECTORS["email"], tries=2, gap=1)
            reject_blocked(page)                           # 先确认网络可访问，避免封禁节点白白申请临时邮箱
            need(ready, "注册页没有出现邮箱输入框")
            reject_phone_mode(page)                        # 手机号分流节点无法走邮箱流程，尽早失败

            with mail_type(channel=channel, proxy=settings.proxy, timeout=20) as mail:
                email = make_email(mail)                    # 页面确认可注册后才申请真实邮箱

                fields = {
                    SELECTORS["email"]: email,
                    SELECTORS["password"]: person["password"],
                    SELECTORS["password_again"]: person["password"],
                }
                need(page.fill_form(fields, verify=True, tries=3), "注册资料填写失败")
                log.info(f"注册邮箱：{email}")
                ensure_email_accepted(page)                 # 填写后立刻检查邮箱是否被页面接受

                need(
                    send_code(page, settings.page_timeout, settings.challenge_timeout),
                    "发送验证码失败，按钮未进入倒计时",
                )

                code = wait_code(mail, timeout=settings.mail_timeout, interval=3, page=page)
                if not code:
                    detail = mail.error or "邮箱内无匹配邮件"
                    raise EmailUnavailable(f"没有收到有效验证码：{detail}")
                log.info(f"收到验证码：{code}")

                need(page.fill(SELECTORS["code"], code, verify=True, tries=3), "验证码填写失败")
                token = submit_registration(page, settings.page_timeout)
                need(token, "注册完成后没有找到 userToken")
        except EmailRejected:
            domain = email.rsplit("@", 1)[-1].lower()      # 从被拒邮箱里取出域名用于记录
            record_bad_domain(domain)                       # 记录不可用域名后再向上抛，交给外层换邮箱重试
            raise
        except Exception:
            shot = failure_path()                          # 每次失败使用独立截图名，便于排查选择器变化
            page.shot(str(shot))                           # 截图失败不会覆盖原异常
            log.error(f"注册失败截图：{shot}")
            raise

    line_type(settings.output).add(token)                  # 浏览器关闭后原子去重保存 token
    table_type(settings.accounts).add(                      # 同时保存完整账号，方便下游直接登录
        {"email": email, "password": person["password"], "token": token}
    )
    log.info(f"注册成功：{email}，token={token[:12]}...")
    return {"email": email, "password": person["password"], "token": token}


# --- 只检查注册页入口，不填写或提交 ---
def check_site(settings, browser_type=Browser):
    with browser_type(headless=settings.headless, proxy=settings.proxy) as page:
        ready = page.open(SIGN_UP_URL, appear=SELECTORS["email"], tries=2, gap=1)
        try:
            reject_blocked(page)                           # Cloudflare/IP 拒绝应和页面结构变化明确区分
        except RuntimeError as error:
            log.error(str(error))                          # 检查模式输出准确网络诊断
            ready = False
        if ready:
            try:
                reject_phone_mode(page)                    # 手机号分流也属于站点不可用
            except RuntimeError as error:
                log.error(str(error))
                ready = False
        if not ready:
            page.shot(str(failure_path("check")))          # 检查失败也留下页面截图
        return ready                                       # 外层子进程把结果传回主进程


# --- 识别 DeepSeek 的网络节点封禁页 ---
def reject_blocked(page):
    text = page.text().lower()                             # Cloudflare 拒绝页正文稳定包含 blocked/unable to access
    blocked = "you have been blocked" in text or "unable to access" in text
    if blocked:
        raise RuntimeError("当前网络节点被拦截，请更换代理后重试")


# --- 条件不满足时用一句可读原因中止当前账号 ---
def need(value, message):
    if not value:                                          # False、空串、None 都表示该步骤没有完成
        raise RuntimeError(message)
    return value                                           # 成功值原样返回，必要时可继续链式使用


# --- 生成失败截图路径 ---
def failure_path(kind="register"):
    folder = Path("output") / "deepseek"                   # output/ 已被 gitignore，不污染仓库
    folder.mkdir(parents=True, exist_ok=True)              # 首次失败时自动建立目录
    stamp = time.strftime("%Y%m%d-%H%M%S")                 # 时间戳区分多次失败
    return folder / f"{kind}-failed-{stamp}.png"


# --- 子进程入口：把成功或错误压成可传回主进程的字典 ---
def worker(settings, result):
    try:
        ok = check_site(settings) if settings.check else bool(register(settings))
        result.put({"ok": ok, "error": ""})               # Queue 只传轻量状态，不传浏览器对象
    except Exception as error:
        log.exception(f"子进程执行失败：{error}")
        result.put({"ok": False, "error": str(error)})    # 主进程统一统计失败


# --- 在硬超时内运行一次检查或注册 ---
def run_limited(settings):
    context = multiprocessing.get_context("spawn")         # Windows/Linux 使用一致的干净子进程语义
    result = context.Queue(maxsize=1)                      # 一次任务只会返回一个结果
    process = context.Process(target=worker, args=(settings, result), daemon=False)
    process.start()                                        # 启动后主进程只负责计时和回收
    process.join(settings.flow_timeout)                    # 最多等待配置的硬超时秒数

    if process.is_alive():
        stop_tree(process)                                 # 超时必须连 Chrome 后代一起结束
        result.close()                                     # 超时路径也关闭 Queue，防止后台管道拖住主进程
        result.join_thread()
        log.error(f"流程超过 {settings.flow_timeout} 秒，已强制结束浏览器进程树")
        return False

    try:
        item = result.get(timeout=2)                       # 子进程正常结束后最多等 2 秒读取 Queue
    except queue.Empty:
        log.error(f"子进程退出但没有返回结果，exit_code={process.exitcode}")
        return False
    finally:
        result.close()                                     # 关闭 Queue 管道，不让后台线程拖住终端退出
        result.join_thread()

    if not item["ok"]:
        log.error(item["error"] or "流程未完成")            # 主进程输出单行失败摘要
    return bool(item["ok"])


# --- 强制结束子进程及其浏览器后代 ---
def stop_tree(process):
    if os.name == "nt":                                   # Windows 的 terminate 不会自动清理 Chrome 子进程
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()                            # taskkill 不可用时退回 Python 终止
    else:
        process.terminate()                                # POSIX 下先终止直接子进程
    process.join(5)                                        # 给系统 5 秒回收句柄
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()                                     # 仍未退出时执行最终强杀
        process.join(2)


# --- 顺序批量运行，避免多个浏览器同时争抢资源 ---
def run_batch(settings):
    if settings.forever:                                  # 无限筛选模式：一直尝试，直到手动 Ctrl+C 停止
        index = 0
        while True:
            index += 1                                    # 轮次从 1 开始累计
            log.info(f"开始第 {index} 次筛选尝试")
            run_limited(settings)                          # 每轮独立硬超时执行，成功和失败都继续下一轮
            time.sleep(settings.gap)                       # 每轮之间留出风控冷却时间
        # 永远不会走到这里，靠 KeyboardInterrupt 结束进程

    success = 0                                            # 成功计数
    for index in range(settings.count):
        log.info(f"开始 {index + 1}/{settings.count}")      # 每个账号开始前输出位置
        success += int(run_limited(settings))              # 独立硬超时执行并累计结果
        if index < settings.count - 1:
            time.sleep(settings.gap)                       # 两个账号之间留出风控冷却时间
    failed = settings.count - success                      # 总数减成功数得到失败数
    log.info(f"执行结束：成功={success}，失败={failed}，总数={settings.count}")
    return failed == 0                                     # 全部成功时进程退出码为 0


# --- 把正整数参数提前校验，拒绝 0 和负数 ---
def positive(value):
    number = int(value)                                    # argparse 会展示 ValueError 为非法参数
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


# --- 解析非交互命令行参数 ---
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="使用 LazyFox 注册 DeepSeek 账号")
    parser.add_argument("--count", type=positive, default=1, help="注册数量，默认 1")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="是否无界面运行 Chrome")
    parser.add_argument("--proxy", default=None, help="浏览器和邮箱代理，如 http://127.0.0.1:7890")
    parser.add_argument("--mail-channel", default="emailnator", help="临时邮箱渠道，默认 emailnator 提供 gmail 邮箱，可传 random 随机选择")
    parser.add_argument("--mail-timeout", type=positive, default=120, help="等待验证码秒数")
    parser.add_argument("--email-attempts", type=positive, default=3, help="邮箱域名拒绝后的总尝试次数")
    parser.add_argument("--page-timeout", type=positive, default=30, help="页面阶段等待秒数")
    parser.add_argument("--challenge-timeout", type=positive, default=120, help="等待手动真人验证秒数")
    parser.add_argument("--flow-timeout", type=positive, default=300, help="单账号硬超时秒数")
    parser.add_argument("--gap", type=float, default=3.0, help="账号之间等待秒数")
    parser.add_argument("--output", default="token/deepseek-tokens.txt", help="token 输出文件")
    parser.add_argument("--accounts", default="accounts/deepseek-accounts.json", help="完整账号输出文件")
    parser.add_argument("--forever", action="store_true", help="无限循环筛选不可用邮箱域名，直到手动停止")
    parser.add_argument("--check", action="store_true", help="只检查注册入口，不填写或提交")
    return parser.parse_args(argv)


# --- 命令行参数转成流程配置 ---
def settings_from(args):
    return Settings(
        count=args.count,
        headless=args.headless,
        proxy=args.proxy,
        mail_channel=args.mail_channel,
        mail_timeout=args.mail_timeout,
        email_attempts=args.email_attempts,
        page_timeout=args.page_timeout,
        challenge_timeout=args.challenge_timeout,
        flow_timeout=args.flow_timeout,
        gap=max(0, args.gap),                               # 负间隔统一归零
        output=args.output,
        accounts=args.accounts,
        forever=args.forever,
        check=args.check,
    )


def main(argv=None):
    settings = settings_from(parse_args(argv))             # 参数一次转换成明确配置对象
    return 0 if run_batch(settings) else 1                 # 退出码便于 shell/定时任务判断成功


if __name__ == "__main__":
    raise SystemExit(main())                                # 无 input()，执行结束后终端自然退出
