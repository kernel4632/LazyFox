"""
临时邮箱工具：一个对象搞定"申请邮箱 → 等邮件 → 取验证码/链接"的完整流程。

设计思想：
逆向注册几乎都要收验证码邮件。底层用官方的 tempemail-sdk（覆盖 270+ 家临时邮箱服务，
返回格式已统一），本文件在它之上再包一层，专门解决三个痛点：
1. 可控——想指定用哪家服务就指定，不想管就随机挑一家能用的；代理、超时、跳过 SSL 一行配置。
2. 智能等待——注册时点了"发送验证码"后，邮件不会秒到。这里提供轮询式等待，
   邮件到了立刻返回，没到就按节奏重试，超时才放弃，调用方不用自己写 while 循环。
3. 直接出结果——常见需求不是"给我邮件列表"，而是"给我那串验证码"。所以直接提供
   wait_code / wait_link，等到并提取好再返回。

里面有什么：
- Mail 类                 一次注册流程对应一个 Mail 实例，持有当前邮箱地址
- Mail.create()           申请一个新邮箱，返回邮箱地址
- Mail.latest()           取收件箱里最新一封邮件
- Mail.wait_mail()        轮询等待一封新邮件到达
- Mail.wait_code()        轮询等待并提取验证码（最常用）
- Mail.wait_link()        轮询等待并提取验证链接

怎么调用：
    from tools.mail import Mail

    # 随机挑一家可用的临时邮箱服务
    with Mail() as mail:
        address = mail.create()                     # 申请邮箱，得到地址
        # ...在网页上填入 address 并点击发送验证码...
        code = mail.wait_code(timeout=120)          # 最多等 120 秒，拿到验证码

    # 指定服务商 + 走代理
    with Mail(channel="guerrillamail", proxy="http://127.0.0.1:7890") as mail:
        address = mail.create()
        link = mail.wait_link(keyword="verify")     # 等一封含 verify 的验证链接
"""

import threading                                             # SDK 配置是全局的，用锁保证多个 Mail 实例不会串代理配置
import time                                                  # 用于轮询时的间隔等待和超时计时
import tempmail_sdk as sdk                                   # 官方临时邮箱 SDK，负责真正跟各家邮箱服务通信
from tools.code import find_code, find_link, strip_tags     # 复用验证码/链接提取逻辑，本文件不重复造轮子


# 全局只需配置一次：关掉 SDK 默认开启的匿名遥测
# 用模块级标记记录是否已做过首次配置；只有首次创建 Mail 时设一次遥测关闭，
# 后续的 proxy/timeout/insecure 只有在跟上次不同时才重设，避免多实例互相覆盖
_last_config = None                                          # 记住上次的配置参数，相同则跳过，不同才更新
_sdk_lock = threading.RLock()                                # 配置和请求必须在同一把锁内完成，防止并发实例互相覆盖


# --- 配置 SDK 全局行为（仅在参数变化时才真正调用 set_config） ---
def setup_sdk(proxy, timeout, insecure):
    # proxy：代理地址，None 表示不走代理
    # timeout：单次网络请求的超时秒数
    # insecure：是否跳过 SSL 证书校验，调试抓包时会用到
    global _last_config

    current = (proxy, timeout, insecure)                    # 把本次参数打包为元组，用于和上次比较
    if current == _last_config:                              # 参数没变化就跳过，不重复调用 SDK 的 set_config
        return

    settings = {"telemetry_enabled": False, "timeout": timeout}  # 永久关闭遥测 + 设置超时
    if proxy:                                                # 只有传了代理才加进配置
        settings["proxy"] = proxy
    if insecure:                                             # 只有明确要求才跳过 SSL
        settings["insecure"] = True

    sdk.set_config(**settings)                              # 只在参数变化时才真正推给 SDK
    _last_config = current                                  # 记住本次参数，下次对比用


class Mail:
    """一次注册流程用一个 Mail 实例，封装邮箱申请和邮件等待。"""

    # --- 初始化：记录本次要用的服务商和网络配置 ---
    def __init__(self, channel=None, proxy=None, timeout=20, insecure=False):
        # channel：指定临时邮箱服务商标识（如 "guerrillamail"），None 表示随机挑一家可用的
        # proxy：代理地址，方便在需要换 IP 的场景下使用
        # timeout：单次网络请求超时秒数
        # insecure：是否跳过 SSL 校验
        self.channel = channel                             # 保存服务商选择，create 时用到
        self.proxy = proxy                                 # 保存本实例代理，每次请求前恢复，避免被其他实例覆盖
        self.timeout = timeout                             # 保存单次请求超时
        self.insecure = insecure                           # 保存 SSL 开关
        self.address = ""                                  # 当前邮箱地址，create 成功后填入
        self.info = None                                   # SDK 返回的邮箱对象，取邮件时要传回给 SDK
        self.seen_ids = set()                              # 已读过的邮件 id 集合，用来区分"新邮件"和"旧邮件"
        self.error = ""                                    # 最近一次网络错误，轮询失败时可供调用方诊断

    # --- 申请一个新邮箱 ---
    def create(self, domain=None, duration=30, max_channels=20, total_timeout=60, suffix=None, domains=None, tries=2, gap=1.0):
        # domain / domains：指定单个域名或优先域名列表；是否支持由具体渠道决定
        # duration：邮箱有效分钟数；支持有效期的渠道会采用
        # max_channels：随机模式最多尝试多少个渠道
        # total_timeout：整次申请的总超时秒数
        # suffix：指定邮箱用户名后缀（支持该能力的渠道会采用）
        retry = sdk.RetryConfig(max_retries=tries, initial_delay=gap, timeout=self.timeout)  # 控制单渠道请求重试
        options = sdk.GenerateEmailOptions(                # 把 SDK 高级能力原样开放，同时保留简单默认值
            channel=self.channel,
            domain=domain,
            duration=duration,
            max_channels_tried=max_channels,
            total_timeout=total_timeout,
            suffix=suffix,
            domains=domains,
            retry=retry,
        )
        with _sdk_lock:                                    # 锁住“设配置+请求”整体，其他 Mail 无法中途改代理
            setup_sdk(self.proxy, self.timeout, self.insecure)  # 恢复本实例配置
            info = sdk.generate_email(options)             # 向 SDK 申请邮箱，可能因服务商全部不可用而返回 None

        if not info:                                       # 申请失败：所有尝试的服务商都没成功
            raise RuntimeError("临时邮箱申请失败，可能是网络问题或服务商全部不可用")

        self.info = info                                   # 保存邮箱对象，后续 latest / wait 都要用它
        self.address = info.email                          # 取出邮箱地址字符串，供调用方填进注册表单
        self.channel = info.channel                        # 回填实际使用的服务商（随机模式下这里才知道用了谁）
        return self.address                                # 返回邮箱地址，让调用方立即可用

    # --- 拉取收件箱当前所有邮件 ---
    def fetch(self, strict=False):
        # strict：False 时网络波动返回空列表供轮询继续；True 时直接抛错便于调试
        if not self.info:                                  # 还没申请邮箱就想收信，属于调用顺序错误
            raise RuntimeError("请先调用 create() 申请邮箱")

        try:
            with _sdk_lock:                                # 拉取期间禁止其他实例切换全局 SDK 配置
                setup_sdk(self.proxy, self.timeout, self.insecure)  # 每次操作前恢复本实例配置
                result = sdk.get_emails(self.info)         # 向 SDK 拉取当前收件箱，返回统一格式的结果对象
        except Exception as error:
            self.error = str(error)                        # 保存真实失败原因，调用方可读取 mail.error
            if strict:
                raise                                      # 严格模式用于调试，保留原异常和堆栈
            return []                                      # 默认容错，让 wait_code 在下一轮继续尝试
        if not result.success:                             # 拉取失败（网络波动等），当作暂时没有邮件处理
            self.error = "临时邮箱服务返回拉取失败"          # 没有异常对象时给出可读原因
            return []
        self.error = ""                                    # 成功请求后清空旧错误
        return result.emails                               # 返回邮件列表，每封含 subject/text/html 等统一字段

    # --- 取最新一封邮件 ---
    def latest(self):
        emails = self.fetch()                              # 拉取全部邮件
        if not emails:                                     # 收件箱为空，返回 None 让调用方知道还没来信
            return None
        return emails[0]                                   # SDK 已按时间排序，第一封即最新

    # --- 轮询等待一封"新"邮件到达 ---
    def wait_mail(self, timeout=120, interval=3, subject="", sender="", check=None):
        # timeout：最多等待多少秒，超过就放弃
        # interval：两次检查之间隔多少秒，太短会给服务商压力，太长会拖慢流程
        # subject / sender：只接受主题或发件人包含指定文字的邮件
        # check：自定义过滤函数，接收邮件对象并返回 True/False
        deadline = time.time() + timeout                   # 算出截止时间点，之后每轮和它比较

        while time.time() < deadline:                      # 没到截止时间就持续检查
            for mail in self.fetch():                      # 遍历当前收件箱每封邮件
                mail_id = mail.id or (mail.subject + mail.date)  # 优先用邮件 id 做唯一标识，没有 id 就用主题+时间兜底
                if mail_id in self.seen_ids:               # 这封之前处理过，跳过，只关心新到的
                    continue
                if subject and subject.lower() not in mail.subject.lower():  # 主题不符合时忽略这封
                    continue
                from_addr = getattr(mail, "from_addr", "")  # SDK 统一使用 from_addr 保存发件人
                if sender and sender.lower() not in from_addr.lower():  # 发件人不符合时忽略
                    continue
                if check and not check(mail):              # 调用方自定义规则未通过时忽略
                    continue
                self.seen_ids.add(mail_id)                 # 只有真正返回的邮件才消费；过滤未命中邮件仍可被后续规则读取
                return mail                                # 发现新邮件，立即返回，不必等满 timeout

            time.sleep(interval)                           # 本轮没有新邮件，歇一会再查，降低请求频率

        return None                                        # 等到超时都没有新邮件，返回 None 表示放弃

    # --- 等待并提取验证码（最常用） ---
    def wait_code(self, timeout=120, interval=3, subject="", sender=""):
        # timeout / interval：含义同 wait_mail
        deadline = time.time() + timeout                   # 整个"等码"过程共用一个截止时间

        while time.time() < deadline:                      # 反复尝试直到超时
            mail = self.wait_mail(
                timeout=deadline - time.time(), interval=interval, subject=subject, sender=sender
            )                                               # 用剩余时间等待符合来源条件的新邮件
            if not mail:                                   # 剩余时间内没等到新邮件，说明整体超时了
                break

            body = mail.text or strip_tags(mail.html)      # 优先用纯文本正文，没有就把 HTML 洗成纯文字
            full = mail.subject + "\n" + body              # 主题也拼进来，有些站把验证码放在主题里
            code = find_code(full)                         # 交给提取工具按常见格式挖验证码
            if code:                                       # 挖到了就是我们要的，直接返回
                return code
            # 挖不到说明这封是无关邮件（如欢迎信），继续循环等下一封新邮件

        return ""                                          # 超时仍没拿到验证码，返回空串让调用方走失败分支

    # --- 等待并提取验证链接 ---
    def wait_link(self, keyword="", timeout=120, interval=3, subject="", sender=""):
        # keyword：只要含该关键词的链接，比如 "verify" 只挑验证链接
        # timeout / interval：含义同 wait_mail
        deadline = time.time() + timeout                   # 共用截止时间

        while time.time() < deadline:                      # 反复尝试直到超时
            mail = self.wait_mail(
                timeout=deadline - time.time(), interval=interval, subject=subject, sender=sender
            )                                               # 等待符合来源条件的新邮件
            if not mail:                                   # 没等到新邮件即整体超时
                break

            source = mail.html or mail.text                # 链接通常在 HTML 里，优先用 HTML 源，退回纯文本
            link = find_link(source, keyword=keyword)      # 按关键词挑出目标链接
            if link:                                       # 找到符合条件的链接就返回
                return link

        return ""                                          # 超时未找到，返回空串

    # --- 列出 SDK 当前支持的临时邮箱渠道 ---
    @staticmethod
    def channels(search=""):
        # search：按渠道标识、名称或网站模糊过滤，留空返回全部 270+ 个渠道
        items = sdk.list_channels()                        # SDK 返回统一 ChannelInfo 列表
        if not search:
            return items                                   # 无过滤时原样返回全部渠道
        word = search.lower()                              # 忽略大小写进行搜索
        return [
            item
            for item in items
            if word in item.channel.lower() or word in item.name.lower() or word in item.website.lower()
        ]

    # --- 支持 with 语法：进入时返回自身 ---
    def __enter__(self):
        return self                                        # with Mail() as mail 时把实例交给 mail

    # --- 支持 with 语法：退出时无需清理（SDK 无长连接） ---
    def __exit__(self, exc_type, exc_value, traceback):
        return False                                       # 不吞异常，让 with 块里的报错正常抛出
