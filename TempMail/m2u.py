import httpx                                                                          # HTTP 客户端库用于发送网络请求和建立流式连接
import re                                                                             # 正则表达式库用于从文本中提取验证码和链接
import time                                                                           # 时间库用于轮询等待和超时控制
import json                                                                           # JSON 库用于解析 EventStream 中的 data 数据


"""
# 拿验证码
with TempMail() as mail:
    print(mail.generateEmail())                                                       # 生成临时邮箱地址
    print(mail.getCode())                                                             # 等待新邮件并自动提取验证码

# 拿激活链接
with TempMail() as mail:
    mail.generateEmail()                                                              # 先生成邮箱
    print(mail.getLink(keyword="activate"))                                           # 等待新邮件并自动提取链接

# 手动翻邮件
with TempMail() as mail:
    mail.generateEmail()                                                              # 先生成邮箱
    item = mail.waitNewMail(timeoutSeconds=60, pollSeconds=2)                         # 等待一封新邮件
    if item:
        body = mail.readMessage(item.get("messageID"))                                # 读取邮件正文
        print(mail.findCode(item.get("subject", "") + "\\n" + body))                  # 从主题+正文中找验证码
        print(mail.findLink(body))                                                    # 从正文中找链接
"""


class TempMail:
    """临时邮箱模块（适配 m2u.io）"""

    def __init__(self, apiUrl="https://api.m2u.io", refererOrigin="https://m2u.io"):
        self.apiUrl = apiUrl                                                          # 邮箱服务 API 根地址
        self.refererOrigin = refererOrigin                                            # 前端页面来源地址
        self.mailboxId = ""                                                           # 邮箱资源 id
        self.token = ""                                                               # 邮箱 token，用于路径中的 mailbox 标识
        self.viewToken = ""                                                           # view_token，用于查看邮件列表和详情
        self.email = ""                                                               # 当前邮箱完整地址
        self.localPart = ""                                                           # 邮箱前缀
        self.domain = ""                                                              # 邮箱域名
        self.seenIds = set()                                                          # 已经读过的邮件编号集合
        self.baselineIds = set()                                                      # 为兼容原规范保留的基线集合
        self.mailMap = {}                                                             # messageID 到邮件对象的映射
        self.mailQueue = []                                                           # SSE 到达后缓存的新邮件队列
        self.queueIds = set()                                                         # 队列中已存在的邮件编号集合
        self.client = httpx.Client(                                                   # 创建浏览器模拟客户端
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",  # 浏览器标识
                "Accept": "*/*",                                                      # 默认接受任意响应类型
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",# 模拟浏览器语言偏好
                "Origin": self.refererOrigin,                                         # 跨域接口常要求带 origin
                "Referer": self.refererOrigin + "/",                                  # 模拟从官网页面发起请求
                "Content-Type": "application/json",                                   # 默认按 JSON 发送请求体
                "DNT": "1",                                                           # Do Not Track 头，保持与抓包一致
            },
            timeout=30,                                                               # 普通请求单次最多等待 30 秒
            follow_redirects=True,                                                    # 自动跟随重定向
        )

    # ==================== 算子层 ====================

    def sendRequest(self, method, path, params=None, body=None, headers=None, retryCount=3):  # 发送标准 HTTP 请求
        lastError = None                                                              # 记录最后一次错误用于最终抛错
        for attempt in range(retryCount):                                             # 最多重试指定次数
            try:
                requestHeaders = {} if headers is None else dict(headers)             # 复制请求头避免修改外部对象
                response = self.client.request(                                       # 发起 HTTP 请求
                    method=method,                                                    # 请求方法如 GET / POST
                    url=self.apiUrl + path,                                           # 拼接完整请求地址
                    params=params,                                                    # URL 查询参数
                    json=body,                                                        # JSON 请求体
                    headers=requestHeaders if requestHeaders else None,               # 若传入额外头则覆盖合并
                )
                response.raise_for_status()                                           # 请求失败时直接抛错方便定位问题
                return response                                                      # 返回完整响应对象
            except (httpx.ConnectError, httpx.ReadTimeout) as e:                       # 网络临时错误（SSL中断、读取超时）时重试
                lastError = e                                                        # 记录错误
                if attempt < retryCount - 1:                                         # 不是最后一次尝试
                    waitSeconds = 0.5 * (2 ** attempt)                              # 指数退避：0.5s, 1s, 2s...
                    time.sleep(waitSeconds)                                           # 等待后重试
                    continue                                                         # 继续下一次重试
                break                                                                # 已达重试次数，退出循环
        raise lastError if lastError else RuntimeError(f"请求失败: {method} {path}")  # 最终抛错

    def fetchMailbox(self):                                                           # 请求服务端自动创建一个临时邮箱
        response = self.sendRequest(                                                  # 调用自动创建邮箱接口
            "POST",
            "/v1/mailboxes/auto",
            body={"domain": None, "lang": "zh-CN"},                                   # 这里按抓包长度给出一个轻量 JSON 体，保持 POST 语义
        )
        data = response.json()                                                        # 解析返回的 JSON 数据
        mailbox = data.get("mailbox", {})                                             # 取出 mailbox 字段
        if not isinstance(mailbox, dict) or not mailbox:                              # mailbox 不存在则说明响应格式异常
            raise RuntimeError(f"invalid mailbox response: {data}")                   # 直接抛错避免后续状态错乱

        self.mailboxId = str(mailbox.get("id", ""))                                   # 保存邮箱资源 id
        self.token = str(mailbox.get("token", ""))                                    # 保存邮箱 token
        self.viewToken = str(mailbox.get("view_token", ""))                           # 保存 view_token
        self.localPart = str(mailbox.get("local_part", ""))                           # 保存邮箱前缀
        self.domain = str(mailbox.get("domain", ""))                                  # 保存邮箱域名
        self.email = f"{self.localPart}@{self.domain}" if self.localPart and self.domain else ""# 拼接完整邮箱地址

        return mailbox                                                                # 返回 mailbox 原始对象

    def fetchMessageList(self):                                                       # 拉取当前邮箱的邮件列表
        if not self.token or not self.viewToken: return []                            # 缺少 token 或 view_token 时无法查询邮件
        response = self.sendRequest(                                                  # 调用邮件列表接口
            "GET",
            f"/v1/mailboxes/{self.token}/messages",
            params={"view": self.viewToken},                                          # 按抓包要求通过 view 参数访问
        )
        data = response.json()                                                        # 解析返回 JSON
        messageList = data.get("messages", [])                                        # 从 messages 字段中取出邮件数组
        if not isinstance(messageList, list): return []                               # 结构异常时返回空列表
        return messageList                                                            # 返回原始邮件列表

    def fetchMessageBody(self, messageId):                                            # 拉取单封邮件的详情正文
        if not self.token or not self.viewToken or not messageId: return {}           # 缺少必要参数时直接返回空字典
        response = self.sendRequest(                                                  # 调用邮件详情接口
            "GET",
            f"/v1/mailboxes/{self.token}/messages/{messageId}",
            params={"view": self.viewToken},                                          # 详情接口同样需要 view_token
        )
        data = response.json()                                                        # 解析返回 JSON
        message = data.get("message", {})                                             # 取出 message 字段
        if not isinstance(message, dict): return {}                                   # 结构异常时返回空字典
        return message                                                                # 返回邮件详情对象

    def normalizeMessage(self, item):                                                 # 统一服务端邮件结构为 TempMail 内部结构
        if not isinstance(item, dict): return None                                    # 格式异常时直接返回空

        messageId = str(item.get("id", ""))                                           # 服务端邮件唯一编号
        sender = str(item.get("from_addr", item.get("from", "")))                     # 优先读取 from_addr 字段
        subject = str(item.get("subject", ""))                                        # 读取邮件主题
        date = str(item.get("received_at", item.get("receivedAt", "")))               # 兼容两种时间字段命名
        textBody = str(item.get("text_body", ""))                                     # 读取纯文本正文
        htmlBody = str(item.get("html_body", ""))                                     # 读取 HTML 正文

        body = textBody if textBody else htmlBody                                     # 优先使用 text_body，缺失时退回 html_body

        normalized = {                                                                # 构造内部统一邮件结构
            "messageID": messageId,                                                   # 邮件编号字段保持与旧规范一致
            "subject": subject,                                                       # 主题
            "from": sender,                                                           # 发件人
            "date": date,                                                             # 收件时间
            "body": body,                                                             # 正文内容
            "textBody": textBody,                                                     # 额外保留文本正文
            "htmlBody": htmlBody,                                                     # 额外保留 HTML 正文
        }
        return normalized                                                             # 返回标准邮件对象

    def findCode(self, text):                                                         # 从文本中找出常见验证码
        if not text: return None                                                      # 空文本时直接返回空

        patterns = [                                                                  # 按优先级定义常见验证码模式
            r"\b[A-Z0-9]{3,4}-[A-Z0-9]{3,4}\b",                                       # 先匹配类似 6I1-ICU 这种带连字符的验证码
            r"\b\d{6}\b",                                                             # 再匹配独立的 6 位数字验证码
            r"\b\d{4,8}\b",                                                           # 最后兜底匹配 4 到 8 位数字
        ]
        for pattern in patterns:                                                      # 依次尝试每种模式
            match = re.search(pattern, text, re.IGNORECASE)                           # 忽略大小写进行搜索
            if match: return match.group(0)                                           # 一旦找到就返回第一个匹配结果
        return None                                                                   # 全部没找到时返回空

    def findLink(self, text, keyword=""):                                             # 从文本中找出链接地址
        allLinks = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text or "")           # 提取所有 http/https 开头的链接
        if keyword: allLinks = [x for x in allLinks if keyword in x]                  # 指定关键词时只保留包含关键词的链接
        if not allLinks: return None                                                  # 没找到时返回空
        return allLinks[0]                                                            # 返回第一个匹配的链接

    def streamOneEvent(self, timeoutSeconds=60):                                      # 建立一次 SSE 连接并等待一个有效事件
        if not self.token or not self.viewToken: return None                          # 没有邮箱 token 时无法监听流

        headers = {                                                                   # 为 SSE 单独准备请求头
            "Accept": "text/event-stream",                                            # 明确声明接收 EventStream
            "Cache-Control": "no-cache",                                              # 禁止缓存保持实时性
            "Origin": self.refererOrigin,                                             # 保持与抓包一致的 origin
            "Referer": self.refererOrigin + "/",                                      # 保持与抓包一致的 referer
        }

        startTime = time.time()                                                       # 记录开始监听时间
        currentEvent = ""                                                             # 当前 SSE 事件名
        currentDataLines = []                                                         # 当前 SSE 事件的数据行缓存

        with self.client.stream(                                                      # 建立流式 HTTP 连接
            "GET",
            self.apiUrl + f"/v1/mailboxes/{self.token}/stream",
            params={"view": self.viewToken},                                          # 按抓包要求传 view 参数
            headers=headers,                                                          # 带上 EventStream 请求头
            timeout=httpx.Timeout(connect=10, read=timeoutSeconds + 5, write=10, pool=10),# 对流式连接设置更合适的超时
        ) as response:
            response.raise_for_status()                                               # 建连失败时直接报错

            for rawLine in response.iter_lines():                                     # 逐行读取 SSE 返回内容
                if time.time() - startTime >= timeoutSeconds:                         # 到达总超时时间就停止等待
                    break                                                             # 跳出循环并返回空

                if rawLine is None:                                                   # 某些情况下可能读到空对象
                    continue                                                          # 直接跳过

                line = rawLine.strip()                                                # 去掉行首尾空白字符

                if not line:                                                          # 空行表示一个 SSE 事件结束
                    if not currentEvent and not currentDataLines:                      # 如果没有事件名和数据就继续读下一段
                        continue

                    rawData = "\n".join(currentDataLines)                             # 把一个事件的所有 data 行拼成完整文本
                    eventName = currentEvent                                           # 保存当前事件名
                    currentEvent = ""                                                 # 立刻重置事件名缓存
                    currentDataLines = []                                             # 立刻重置数据缓存

                    if eventName == "connected":                                      # connected 只是连接成功提示，不是新邮件
                        continue                                                      # 忽略它继续等待

                    if eventName == "mail":                                           # mail 表示有新邮件事件
                        try:
                            payload = json.loads(rawData)                             # 把 data 后面的 JSON 解析成字典
                        except Exception:
                            continue                                                  # 解析失败就忽略该事件

                        messageId = str(payload.get("id", ""))                        # 从事件中取出邮件 id
                        if not messageId: continue                                    # 没有邮件 id 的事件无意义直接跳过
                        return payload                                                # 返回事件负载给上层处理

                    continue                                                          # 其他未知事件直接忽略

                if line.startswith(":"):                                              # 以冒号开头的是 keep-alive 注释行
                    continue                                                          # 直接忽略

                if line.startswith("event:"):                                         # 事件名行
                    currentEvent = line[6:].strip()                                   # 提取事件名并保存
                    continue                                                          # 继续读取后续 data 行

                if line.startswith("data:"):                                          # 数据行
                    currentDataLines.append(line[5:].strip())                         # 去掉 data: 前缀后缓存

        return None                                                                   # 超时期间没有等到有效事件时返回空

    # ==================== 编排层 ====================

    def generateEmail(self):                                                          # 生成一个新的临时邮箱地址
        self.fetchMailbox()                                                           # 调用底层接口创建邮箱并写入对象状态
        self.seenIds = set()                                                          # 清空已读记录避免新邮箱被旧状态干扰
        self.baselineIds = set()                                                      # 清空基线集合让新邮箱从零开始
        self.mailMap = {}                                                             # 清空邮件映射缓存
        self.mailQueue = []                                                           # 清空新邮件队列
        self.queueIds = set()                                                         # 清空队列编号集合
        return self.email                                                             # 返回完整邮箱地址

    def getInbox(self):                                                               # 获取当前未读的新邮件列表
        if self.mailQueue:                                                            # 如果 SSE 已经缓存了新邮件
            freshList = []                                                            # 准备收集当前仍未读的邮件
            for item in self.mailQueue:                                               # 遍历队列中的邮件
                messageId = item.get("messageID", "")                                 # 取邮件编号
                if not messageId: continue                                            # 没有编号就跳过
                if messageId in self.seenIds: continue                                # 已读邮件跳过
                freshList.append(item)                                                # 剩下的就是未读新邮件
            return freshList                                                          # 直接返回队列中的新邮件

        if not self.token or not self.viewToken: return []                            # 没有邮箱状态时返回空列表

        try:
            messageList = self.fetchMessageList()                                     # 拉取当前完整邮件列表
        except Exception:
            return []                                                                 # 请求失败时返回空不中断流程

        normalizedList = []                                                           # 用于收集规范化后的邮件列表
        for item in messageList:                                                      # 遍历原始邮件摘要
            mail = self.normalizeMessage(item)                                        # 统一邮件结构
            if not mail: continue                                                     # 格式异常时跳过
            messageId = mail.get("messageID", "")                                     # 取出邮件编号
            if messageId: self.mailMap[messageId] = mail                              # 写入映射缓存
            normalizedList.append(mail)                                               # 收集到规范列表中

        if not self.baselineIds:                                                      # 第一次调用时只建立基线
            for item in normalizedList:                                               # 遍历当前所有邮件
                messageId = item.get("messageID", "")                                 # 取出邮件编号
                if messageId: self.baselineIds.add(messageId)                         # 把现有邮件全部视为历史邮件
            return []                                                                 # 首次调用不返回任何邮件

        skipIds = self.seenIds | self.baselineIds                                     # 合并已读和历史基线编号
        freshList = []                                                                # 准备收集真正的新邮件
        for item in normalizedList:                                                   # 遍历规范化后的邮件
            messageId = item.get("messageID", "")                                     # 取出邮件编号
            if not messageId: continue                                                # 没有编号就跳过
            if messageId in skipIds: continue                                         # 已读或历史邮件都跳过
            freshList.append(item)                                                    # 剩下的就是新邮件

        return freshList                                                              # 返回新邮件列表

    def readMessage(self, messageId):                                                 # 读取一封邮件的正文并标记已读
        if not messageId: return ""                                                   # 没有 messageId 时返回空字符串

        cached = self.mailMap.get(str(messageId), {})                                 # 先尝试从缓存中读取摘要或详情
        if cached.get("body"):                                                        # 如果缓存里已经有正文则无需再次请求详情
            self.seenIds.add(str(messageId))                                          # 标记为已读
            self.mailQueue = [x for x in self.mailQueue if x.get("messageID") != str(messageId)]# 从队列中移除
            self.queueIds.discard(str(messageId))                                     # 从队列编号集合中移除
            return str(cached.get("body", ""))                                        # 直接返回缓存中的正文

        detail = self.fetchMessageBody(str(messageId))                                # 调用详情接口获取完整正文
        mail = self.normalizeMessage(detail)                                          # 把详情对象规范化成内部结构
        if not mail: return ""                                                        # 规范化失败时返回空字符串

        self.mailMap[str(messageId)] = mail                                           # 用详情覆盖缓存中的摘要对象
        self.seenIds.add(str(messageId))                                              # 标记为已读
        self.mailQueue = [x for x in self.mailQueue if x.get("messageID") != str(messageId)]# 从队列中移除该邮件
        self.queueIds.discard(str(messageId))                                         # 从队列编号集合中移除

        return str(mail.get("body", ""))                                              # 返回邮件正文

    def waitNewMail(self, timeoutSeconds=60, pollSeconds=2):                          # 等待一封新邮件到达
        freshList = self.getInbox()                                                   # 先看看当前缓存或接口里是否已有未读邮件
        if freshList: return freshList[0]                                             # 如果已有就直接返回第一封

        startTime = time.time()                                                       # 记录开始时间
        while time.time() - startTime < timeoutSeconds:                               # 在总超时时间内循环等待
            remainSeconds = max(1, int(timeoutSeconds - (time.time() - startTime)))   # 计算本轮还能等待多久
            event = self.streamOneEvent(timeoutSeconds=remainSeconds)                 # 开一次 SSE 等待新邮件事件
            if not event:                                                             # 没等到有效事件则稍后继续
                time.sleep(min(pollSeconds, 1))                                       # 短暂休眠后准备重连
                continue

            messageId = str(event.get("id", ""))                                      # 从事件中取出新邮件编号
            if not messageId:                                                         # 没有编号说明事件无效
                time.sleep(min(pollSeconds, 1))                                       # 短暂等待后继续
                continue

            try:
                detail = self.fetchMessageBody(messageId)                             # 直接根据事件中的 id 拉取邮件详情
            except Exception:
                time.sleep(min(pollSeconds, 1))                                       # 详情拉取失败则稍等后继续
                continue

            mail = self.normalizeMessage(detail)                                      # 规范化详情邮件对象
            if not mail:                                                              # 规范化失败则继续等待
                time.sleep(min(pollSeconds, 1))
                continue

            if messageId not in self.queueIds and messageId not in self.seenIds:      # 避免重复加入同一封邮件
                self.mailQueue.append(mail)                                           # 把新邮件压入队列
                self.queueIds.add(messageId)                                          # 记录该邮件已在队列中
                self.mailMap[messageId] = mail                                        # 写入缓存映射

            return mail                                                               # 返回这封刚到达的新邮件

        return None                                                                   # 超时后仍未等到新邮件则返回空

    def getCode(self, timeoutSeconds=60, pollSeconds=2):                              # 等新邮件并自动提取验证码
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                          # 等一封新邮件
        if not mail: return None                                                      # 没等到就返回空

        body = self.readMessage(mail.get("messageID", ""))                            # 读取正文并标记已读
        subject = str(mail.get("subject", ""))                                        # 取出主题用于辅助匹配验证码

        code = self.findCode(subject)                                                 # 先从主题中找验证码
        if code: return code                                                          # 找到就直接返回

        code = self.findCode(body)                                                    # 主题中没有再从正文中找
        if code: return code                                                          # 找到就返回

        return self.findCode(subject + "\n" + body)                                   # 最后把主题和正文拼起来统一搜索一次

    def getLink(self, keyword="", timeoutSeconds=60, pollSeconds=2):                  # 等新邮件并自动提取链接
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                          # 等一封新邮件
        if not mail: return None                                                      # 没等到就返回空

        body = self.readMessage(mail.get("messageID", ""))                            # 读取正文并标记已读
        link = self.findLink(body, keyword)                                           # 优先从正文中找链接
        if link: return link                                                          # 找到了就直接返回

        subject = str(mail.get("subject", ""))                                        # 备用地取主题
        return self.findLink(subject, keyword)                                        # 极少数情况下主题里也可能有链接

    def listAll(self):                                                                # 列出所有邮件包括已读的
        if not self.token or not self.viewToken: return []                            # 没有邮箱状态时返回空列表
        try:
            messageList = self.fetchMessageList()                                     # 拉取完整邮件摘要列表
        except Exception:
            return []                                                                 # 请求失败时返回空列表

        normalizedList = []                                                           # 收集规范化后的完整邮件列表
        for item in messageList:                                                      # 遍历邮件摘要
            mail = self.normalizeMessage(item)                                        # 规范化摘要对象
            if not mail: continue                                                     # 格式异常时跳过
            messageId = mail.get("messageID", "")                                     # 取出邮件编号
            if not messageId: continue                                                # 没有编号就跳过

            if not mail.get("body"):                                                  # 如果摘要里没有正文则请求详情补全
                try:
                    detail = self.fetchMessageBody(messageId)                         # 拉取邮件详情
                    detailMail = self.normalizeMessage(detail)                        # 规范化详情对象
                    if detailMail: mail = detailMail                                  # 用详情覆盖摘要
                except Exception:
                    pass                                                              # 某封邮件详情失败时保留摘要继续

            self.mailMap[messageId] = mail                                            # 写入缓存映射
            normalizedList.append(mail)                                               # 收集到结果列表

        return normalizedList                                                         # 返回完整邮件列表

    def getLatestMail(self):                                                           # 获取最新一封邮件，不区分新旧，适合做调试或读取已存在邮件
        allMailList = self.listAll()                                                   # 直接读取完整邮件列表，避免被基线机制过滤掉旧邮件
        if not allMailList: return None                                                # 没有邮件时返回空
        return allMailList[0]                                                          # 服务端返回通常已按时间倒序排列，第一封就是最新邮件

    def clearMarks(self):                                                             # 清空所有已读和基线标记
        self.seenIds = set()                                                          # 重置已读集合
        self.baselineIds = set()                                                      # 重置基线集合

    def close(self):                                                                  # 关闭客户端释放网络资源
        self.client.close()                                                           # 关闭底层连接池

    def __enter__(self): return self                                                  # 进入 with 语句时返回自身
    def __exit__(self, *args): self.close()                                           # 退出 with 语句时自动关闭
    
    
    
if __name__ == "__main__":
    with TempMail() as mail:
        print(mail.generateEmail())
        print(mail.listAll())