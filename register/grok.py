"""
x.ai 注册示例：展示怎样用 LazyFox 的 Browser、Mail、Person 和 Lines 编写完整注册流程。

这个文件只保留 x.ai 特有内容：注册地址、页面选择器、邮箱拒绝判断和 sso Cookie 名称。
浏览器重试、输入校验、临时邮箱轮询、验证码提取、身份生成、日志和 token 去重保存全部
交给 LazyFox。命令行没有 input()，适合终端、定时任务和自动测试。

运行示例：
    uv run python register/grok.py --count 1 --headless
    uv run python register/grok.py --count 3 --flow-timeout 300
    uv run python register/grok.py --check --headless --flow-timeout 45

安全边界：
每个账号在独立子进程里运行，超过 flow_timeout 后终止整棵进程树，防止浏览器或网络请求
一直挂住终端。--check 只打开注册页并检查入口，不填写或提交任何账号信息。
"""

import argparse                                             # 非交互命令行参数，替代 console.input
import multiprocessing                                     # 每个账号放进独立进程，提供硬超时边界
import os                                                   # 判断系统并在 Windows 上终止完整浏览器进程树
import queue                                                # 安全读取子进程结果，区分超时和无返回
import subprocess                                           # Windows taskkill 用于清理子进程及 Chrome 后代
import time                                                 # 页面阶段轮询和批次间隔
from dataclasses import dataclass                           # 集中表达一次注册的可控参数
from pathlib import Path                                   # 生成失败截图路径

from lazyfox import Browser, Lines, Log, Mail, Person      # 脚手架公开入口：浏览器、邮箱、身份、保存和日志


SIGN_UP_URL = "https://accounts.x.ai/sign-up"              # x.ai 注册入口
BLACKLIST = {"kkb.qzz.io"}                                 # 已知被 x.ai 拒绝的临时邮箱域名
EMAIL_REJECTED = (
    "已被拒绝", "rejected", "not allowed", "unsupported email",
    "找到现有帐户", "找到现有账户", "existing account", "account associated with this email",
)
ACCOUNT_READY = ("管理您的帐户", "管理您的账户", "manage your account", "your account")

# 页面选择器是本示例唯一需要跟随 x.ai 页面变化维护的数据。
SELECTORS = {
    "email_option": (
        "text=使用邮箱注册",                                # 当前中文页面
        "text=Sign up with email",                         # 英文页面
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/div[2]/button[1]",  # 旧页面兜底
    ),
    "email": (
        "css=input[type='email']",                         # 语义属性优先，不依赖页面层级
        "css=input[autocomplete='email']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/input",
    ),
    "email_submit": (
        "css=form button[type='submit']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]",
    ),
    "code": (
        "css=input[autocomplete='one-time-code']",
        "css=input[inputmode='numeric']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/div[1]/div[4]/input",
    ),
    "code_submit": (
        "css=form button[type='submit']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]",
    ),
    "first": (
        "css=input[autocomplete='given-name']",
        "css=input[name='firstName']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[1]/div/input",
    ),
    "last": (
        "css=input[autocomplete='family-name']",
        "css=input[name='lastName']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[2]/div/input",
    ),
    "password": (
        "css=input[type='password']",
        "css=input[autocomplete='new-password']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[2]/div/input",
    ),
    "submit": (
        "css=form button[type='submit']",
        "xpath=/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[3]/button[1]",
    ),
}

log = Log("grok-register")                                # 全流程共用同名彩色日志


class EmailUnavailable(RuntimeError):
    """当前邮箱无法完成注册，可以更换邮箱后安全重试。"""


class EmailRejected(EmailUnavailable):
    """x.ai 明确拒绝临时邮箱域名或邮箱已关联其他账户。"""


@dataclass
class Settings:
    """一次 Grok 注册及其外层硬超时配置。"""

    count: int = 1                                         # 批量账号数量
    headless: bool = False                                 # 是否隐藏浏览器窗口
    proxy: str | None = None                               # 浏览器和临时邮箱共用代理
    mail_channel: str | None = "m2u"                       # 默认沿用原示例的 m2u 渠道，传 random 可随机
    mail_timeout: int = 120                                # 等验证码最长秒数
    email_attempts: int = 3                                # 域名被拒绝时最多换邮箱重开流程次数
    page_timeout: int = 30                                 # 每个页面阶段最长秒数
    challenge_timeout: int = 120                           # 等待用户在有头浏览器完成真人验证的秒数
    flow_timeout: int = 300                                # 单账号子进程硬存活上限
    gap: float = 3.0                                       # 两个账号之间等待秒数
    output: str = "token/grok-tokens.txt"                  # token 保存位置，token/ 已被 gitignore
    check: bool = False                                    # 只检查注册入口，不产生注册行为


# --- 申请一个不在黑名单里的临时邮箱 ---
def make_email(mail, attempts=5):
    for attempt in range(1, attempts + 1):                 # 服务域名随机时允许有限次重选
        address = mail.create(tries=2, total_timeout=45)    # SDK 负责渠道请求重试和总超时
        domain = address.rsplit("@", 1)[-1].lower()         # 只取域名用于黑名单判断
        if domain not in BLACKLIST:
            log.info(f"临时邮箱：{address}")               # 合法地址立即交给注册流程
            return address
        log.warning(f"邮箱域名被列入黑名单，第 {attempt}/{attempts} 次重新申请：{domain}")
    raise RuntimeError("连续申请到被拒绝的临时邮箱域名")     # 有限重试后明确失败，不无限循环


# --- 等待验证码输入阶段，同时识别邮箱域名被拒绝 ---
def wait_code_page(page, timeout):
    deadline = time.monotonic() + timeout                  # 使用单调时钟，不受系统时间修改影响
    while time.monotonic() < deadline:
        if page.visible(SELECTORS["code"], timeout=1):      # 验证码框真正可见才进入收信阶段
            return True
        reject_email(page)                                  # 首次邮箱提交也可能立即拒绝域名
        page.sleep(0.5)                                     # 控制检查频率，避免空转占满 CPU
    return False                                           # 超时交给调用方生成失败截图


# --- 检查页面是否明确拒绝邮箱 ---
def reject_email(page):
    page_text = page.text().lower()                         # 不依赖易变的错误提示元素选择器
    if any(marker in page_text for marker in EMAIL_REJECTED):
        raise EmailRejected("x.ai 拒绝了当前临时邮箱域名")
    return False


# --- 完成注册后先在原页面确认结果 ---
def wait_registration(page, timeout):
    deadline = time.monotonic() + timeout
    account_ready = False                                  # 成功页面出现后仍留在原处等待 Cookie
    while time.monotonic() < deadline:
        token = page.cookie("sso")
        if token:
            return token
        reject_email(page)                                  # 最终提交仍可能拒绝临时邮箱
        page_text = page.text().lower()
        if any(marker in page_text for marker in ACCOUNT_READY):
            if not account_ready:
                account_ready = True
                names = ", ".join(sorted(page.cookies())) or "无"
                log.info(f"已进入账户中心，当前 Cookie 名称：{names}")
        page.sleep(0.5)
    if account_ready:
        raise RuntimeError("账户已创建，但账户页面未读到 sso Cookie")
    raise RuntimeError("完成注册后未确认成功，保留原页面供截图排查")


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
        script = "document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''"
        if page.run_js(script):                             # Turnstile 完成后会写入隐藏响应字段
            log.info("人机验证已完成")
            return True
        page.sleep(0.5)                                     # 有限频率检查，不占满 CPU
    return False


# --- 执行一个完整注册流程 ---
def register(settings, browser_type=Browser, mail_type=Mail, person_type=Person, line_type=Lines):
    for attempt in range(1, settings.email_attempts + 1):  # 所有尝试仍受外层 flow_timeout 硬限制
        try:
            return register_once(settings, browser_type, mail_type, person_type, line_type)
        except EmailUnavailable:
            if attempt >= settings.email_attempts:
                raise                                      # 用尽次数后保留明确错误
            log.warning(f"邮箱域名被拒绝，准备更换邮箱重试 {attempt + 1}/{settings.email_attempts}")
    raise RuntimeError("邮箱重试流程异常结束")              # 理论兜底


# --- 使用一个新浏览器和新邮箱尝试注册一次 ---
def register_once(settings, browser_type=Browser, mail_type=Mail, person_type=Person, line_type=Lines):
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
            ready = page.open(SIGN_UP_URL, appear=SELECTORS["email_option"], tries=2, gap=1)
            reject_blocked(page)                           # 先确认网络可访问，避免封禁节点白白申请临时邮箱
            need(ready, "注册页没有出现邮箱注册入口")

            opened = page.click(
                SELECTORS["email_option"], appear=SELECTORS["email"], tries=10, gap=1
            )
            need(opened, "邮箱注册表单没有出现")

            with mail_type(channel=channel, proxy=settings.proxy, timeout=20) as mail:
                email = make_email(mail)                    # 页面确认可注册后才申请真实邮箱
                need(page.fill(SELECTORS["email"], email, verify=True), "邮箱填写失败")
                submitted = page.click(
                    SELECTORS["email_submit"], appear=SELECTORS["code"],
                    tries=5, gap=1, repeat=False,
                )
                # 点击后仍同时检查拒绝文案；后置条件不能只依赖按钮调用是否返回。
                need(submitted or wait_code_page(page, settings.page_timeout), "等待验证码输入框超时")

                code = mail.wait_code(timeout=settings.mail_timeout, interval=3)
                if not code:
                    detail = mail.error or "邮箱内无匹配邮件"
                    raise EmailUnavailable(f"没有收到有效验证码：{detail}")
                log.info(f"收到验证码：{code}")

                advanced = page.type(
                    SELECTORS["code"], code, delay=0.08,
                    appear=SELECTORS["first"], tries=10, gap=1, repeat=False,
                )
                # 当前页面输入完整 OTP 后自动前进；旧版页面若仍需按钮，则用提交按钮兜底。
                verified = advanced or page.click(
                    SELECTORS["code_submit"], appear=SELECTORS["first"], tries=10, gap=1
                )
                need(verified, "验证码提交后没有进入资料页面")

                fields = {
                    SELECTORS["first"]: person["first"],
                    SELECTORS["last"]: person["last"],
                    SELECTORS["password"]: person["password"],
                }
                need(page.fill_form(fields, verify=True, tries=3), "个人资料填写失败")
                log.info(f"注册姓名：{person['name']}")
                need(wait_challenge(page, settings.challenge_timeout), "等待真人验证超时")

                # 提交按钮默认只真正点击一次，随后由 wait_sso 观察最终 Cookie，避免重复注册。
                need(page.click(SELECTORS["submit"]), "注册提交按钮点击失败")
                page.press("css=body", "Escape")           # 尝试收起可能出现的密码保存浮层
                page.run_js("document.body?.click()")      # 让页面重新获得焦点，方便 Cookie 写入完成
                token = wait_registration(page, settings.page_timeout)
                need(token, "注册完成后没有找到 sso Cookie")
        except Exception:
            shot = failure_path()                          # 每次失败使用独立截图名，便于排查选择器变化
            page.shot(str(shot))                           # 截图失败不会覆盖原异常
            log.error(f"注册失败截图：{shot}")
            raise

    line_type(settings.output).add(token)                  # 浏览器关闭后原子去重保存 token
    log.info(f"注册成功：{email}，sso={token[:12]}...")
    return {"email": email, "password": person["password"], "token": token}


# --- 只检查注册页入口，不填写或提交 ---
def check_site(settings, browser_type=Browser):
    with browser_type(headless=settings.headless, proxy=settings.proxy) as page:
        ready = page.open(SIGN_UP_URL, appear=SELECTORS["email_option"], tries=2, gap=1)
        try:
            reject_blocked(page)                           # Cloudflare/IP 拒绝应和页面结构变化明确区分
        except RuntimeError as error:
            log.error(str(error))                          # 检查模式输出准确网络诊断
            ready = False
        if not ready:
            page.shot(str(failure_path("check")))          # 检查失败也留下页面截图
        return ready                                       # 外层子进程把结果传回主进程


# --- 识别 x.ai 的网络节点封禁页 ---
def reject_blocked(page):
    text = page.text().lower()                             # Cloudflare 拒绝页正文稳定包含 blocked/unable to access
    blocked = "you have been blocked" in text or "unable to access x.ai" in text
    if blocked:
        raise RuntimeError("当前网络节点被 x.ai 拦截，请更换代理后重试")


# --- 条件不满足时用一句可读原因中止当前账号 ---
def need(value, message):
    if not value:                                          # False、空串、None 都表示该步骤没有完成
        raise RuntimeError(message)
    return value                                           # 成功值原样返回，必要时可继续链式使用


# --- 生成失败截图路径 ---
def failure_path(kind="register"):
    folder = Path("output") / "grok"                       # output/ 已被 gitignore，不污染仓库
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
    parser = argparse.ArgumentParser(description="使用 LazyFox 注册 x.ai 账号")
    parser.add_argument("--count", type=positive, default=1, help="注册数量，默认 1")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="是否无界面运行 Chrome")
    parser.add_argument("--proxy", default=None, help="浏览器和邮箱代理，如 http://127.0.0.1:7890")
    parser.add_argument("--mail-channel", default="m2u", help="临时邮箱渠道；random 表示随机")
    parser.add_argument("--mail-timeout", type=positive, default=120, help="等待验证码秒数")
    parser.add_argument("--email-attempts", type=positive, default=3, help="邮箱域名拒绝后的总尝试次数")
    parser.add_argument("--page-timeout", type=positive, default=30, help="页面阶段等待秒数")
    parser.add_argument("--challenge-timeout", type=positive, default=120, help="等待手动真人验证秒数")
    parser.add_argument("--flow-timeout", type=positive, default=300, help="单账号硬超时秒数")
    parser.add_argument("--gap", type=float, default=3.0, help="账号之间等待秒数")
    parser.add_argument("--output", default="token/grok-tokens.txt", help="token 输出文件")
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
        check=args.check,
    )


def main(argv=None):
    settings = settings_from(parse_args(argv))             # 参数一次转换成明确配置对象
    if settings.headless and not settings.check:
        log.error("x.ai 正式注册不支持无头浏览器；请移除 --headless")
        return 2                                           # 在启动邮箱和浏览器前拒绝无效模式
    return 0 if run_batch(settings) else 1                 # 退出码便于 shell/定时任务判断成功


if __name__ == "__main__":
    raise SystemExit(main())                                # 无 input()，执行结束后终端自然退出
