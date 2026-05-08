import httpx                                                                          # HTTP 客户端库用于发送网络请求
import re                                                                              # 正则表达式库用于从文本中提取内容
import time                                                                            # 时间库用于轮询等待和超时控制
from urllib.parse import unquote                                                       # URL 解码工具用于处理令牌中的转义字符

""" 
# 拿验证码
with TempMail() as mail:
    print(mail.generateEmail())
    # ... 去注册页面填这个邮箱，点发送验证码 ...
    print(mail.getCode())

# 拿激活链接
with TempMail() as mail:
    mail.generateEmail()
    print(mail.getLink(keyword="activate"))

# 手动翻邮件
with TempMail() as mail:
    mail.generateEmail()
    time.sleep(10)
    for item in mail.getInbox():
        body = mail.readMessage(item.get("messageID"))
        print(mail.findCode(body))
        print(mail.findLink(body))
"""
class TempMail:
    """临时邮箱模块"""

    def __init__(self, apiUrl="https://www.emailnator.com"):
        self.apiUrl = apiUrl                                                           # 邮箱服务的根地址
        self.token = ""                                                                # 服务端下发的防伪令牌
        self.email = ""                                                                # 当前生成的临时邮箱地址
        self.seenIds = set()                                                           # 已经读过的邮件编号集合
        self.baselineIds = set()                                                       # 生成邮箱时已存在的邮件编号集合
        self.client = httpx.Client(                                                    # 创建浏览器模拟客户端
            headers={
                "User-Agent": "Mozilla/5.0 Chrome/145.0.0.0",                          # 伪装成 Chrome 浏览器
                "X-Requested-With": "XMLHttpRequest",                                  # 标记为页面内部发出的请求
            },
            timeout=30,                                                                # 单次请求最多等 30 秒
            follow_redirects=True,                                                     # 自动跟随页面跳转
        )

    # ==================== 算子层 ====================

    def fetchToken(self):                                                              # 从服务端获取防伪令牌
        self.client.get(self.apiUrl)                                                   # 访问主页让服务端把令牌塞进 cookie
        rawToken = self.client.cookies.get("XSRF-TOKEN", "")                           # 从 cookie 里取出原始令牌
        self.token = unquote(rawToken)                                                 # 解码令牌中的转义字符得到可用值
        return self.token                                                              # 返回令牌给调用方

    def sendRequest(self, method, path, body=None):                                    # 发送带令牌的请求到邮箱服务
        if not self.token: self.fetchToken()                                           # 没有令牌时自动获取一次
        headers = {"X-Xsrf-Token": self.token}                                        # 把令牌放进请求头通过服务端校验
        response = self.client.request(method, self.apiUrl + path,json=body, headers=headers)# 拼接完整地址发送请求
        response.raise_for_status()                                                    # 请求失败时直接报错不静默吞掉
        return response                                                                # 返回完整响应给调用方

    def fetchMessageList(self, email):                                                 # 拉取指定邮箱的邮件列表
        response = self.sendRequest("POST", "/message-list",body={"email": email})      # 调用邮件列表接口
        data = response.json()                                                         # 把返回的文本解析成字典
        messageList = data.get("messageData", [])                                      # 从字典中取出邮件数组
        return messageList                                                             # 返回邮件列表给调用方

    def fetchMessageBody(self, email, messageId):                                      # 拉取单封邮件的正文
        response = self.sendRequest("POST", "/message-list",body={"email": email, "messageID": messageId})# 带上邮件编号请求正文接口
        return response.text                                                           # 返回原始文本给调用方

    def findCode(self, text):                                                          # 从文本中找出 6 位数字验证码
        match = re.search(r"\b\d{6}\b", text)                                         # 匹配独立的 6 位连续数字
        if not match: return None                                                      # 没找到就返回空
        return match.group(0)                                                          # 找到了就返回这 6 位数字

    def findLink(self, text, keyword=""):                                              # 从文本中找出链接地址
        allLinks = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)                  # 提取所有 http/https 开头的链接
        if keyword: allLinks = [x for x in allLinks if keyword in x]                   # 有关键词时只保留包含它的链接
        if not allLinks: return None                                                   # 没找到就返回空
        return allLinks[0]                                                             # 返回第一个匹配的链接

    # ==================== 编排层 ====================

    def generateEmail(self):                                                           # 生成一个新的临时邮箱地址
        # --- 请求服务端生成邮箱 ---
        response = self.sendRequest("POST", "/generate-email", body={"email": ["plusGmail", "dotGmail"]})# 调用生成邮箱接口
        data = response.json().get("email", [])                                        # 从返回数据中取出邮箱字段

        # --- 兼容返回格式 ---
        if isinstance(data, list) and data: self.email = data[0]                       # 返回列表时取第一个
        if isinstance(data, str): self.email = data                                    # 返回字符串时直接用

        # --- 重置邮件追踪状态 ---
        self.seenIds = set()                                                           # 清空已读记录避免新邮箱被旧状态干扰
        self.baselineIds = set()                                                       # 清空基线记录让新邮箱从零开始

        return self.email                                                              # 返回邮箱地址

    def getInbox(self):                                                                # 获取当前未读的新邮件列表
        if not self.email: return []                                                   # 没有邮箱时直接返回空列表

        # --- 拉取邮件列表 ---
        try: messageList = self.fetchMessageList(self.email)                           # 请求邮件列表
        except Exception: return []                                                    # 请求失败返回空不中断流程

        # --- 首次调用时建立基线 ---
        if not self.baselineIds:                                                       # 基线为空说明是第一次调用
            for item in messageList:                                                   # 遍历当前所有邮件
                messageId = item.get("messageID", "")                                  # 取出邮件编号
                if messageId: self.baselineIds.add(messageId)                          # 记入基线集合排除历史邮件
            return []                                                                  # 首次调用不返回邮件

        # --- 过滤出新邮件 ---
        skipIds = self.seenIds | self.baselineIds                                      # 合并已读和基线得到需要跳过的编号
        freshList = []                                                                 # 准备收集新邮件
        for item in messageList:                                                       # 遍历邮件列表
            if not isinstance(item, dict): continue                                    # 跳过格式异常的数据
            messageId = item.get("messageID", "")                                      # 取出邮件编号
            if not messageId: continue                                                 # 没有编号的跳过
            if messageId in skipIds: continue                                          # 已读或基线邮件跳过
            freshList.append(item)                                                     # 收集新邮件

        return freshList                                                               # 返回新邮件列表

    def readMessage(self, messageId):                                                  # 读取一封邮件的正文并标记已读
        if not self.email: return ""                                                   # 没有邮箱时返回空文本
        self.seenIds.add(messageId)                                                    # 把这封邮件标记为已读
        body = self.fetchMessageBody(self.email, messageId)                            # 拉取邮件正文
        return body                                                                    # 返回正文内容

    def waitNewMail(self, timeoutSeconds=60, pollSeconds=2):                           # 等待一封新邮件到达
        startTime = time.time()                                                        # 记录开始时间
        while time.time() - startTime < timeoutSeconds:                                # 在超时时间内循环
            freshList = self.getInbox()                                                # 检查有没有新邮件
            if freshList: return freshList[0]                                          # 有新邮件就返回第一封
            time.sleep(pollSeconds)                                                    # 没有就等一会再查
        return None                                                                    # 超时了返回空

    def getCode(self, timeoutSeconds=60, pollSeconds=2):                               # 等新邮件并自动提取验证码
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                           # 等一封新邮件
        if not mail: return None                                                       # 没等到就返回空
        body = self.readMessage(mail.get("messageID", ""))                             # 读邮件正文
        code = self.findCode(body)                                                     # 从正文里找验证码
        return code                                                                    # 返回验证码

    def getLink(self, keyword="", timeoutSeconds=60, pollSeconds=2):                   # 等新邮件并自动提取链接
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                           # 等一封新邮件
        if not mail: return None                                                       # 没等到就返回空
        body = self.readMessage(mail.get("messageID", ""))                             # 读邮件正文
        link = self.findLink(body, keyword)                                            # 从正文里找链接
        return link                                                                    # 返回链接

    def listAll(self):                                                                 # 列出所有邮件包括已读的
        if not self.email: return []                                                   # 没有邮箱时返回空列表
        try: return self.fetchMessageList(self.email)                                  # 返回完整邮件列表不做过滤
        except Exception: return []                                                    # 请求失败返回空列表

    def getLatestMail(self):                                                           # 获取最新一封邮件，不区分新旧，适合做调试或读取已存在邮件
        allMailList = self.listAll()                                                   # 直接读取完整邮件列表，避免被基线机制过滤掉旧邮件
        if not allMailList: return None                                                # 没有邮件时返回空
        return allMailList[0]                                                          # 服务端返回通常已按时间倒序排列，第一封就是最新邮件

    def clearMarks(self):                                                              # 清空所有已读和基线标记
        self.seenIds = set()                                                           # 重置已读集合
        self.baselineIds = set()                                                       # 重置基线集合

    def close(self):                                                                   # 关闭客户端释放网络资源
        self.client.close()                                                            # 关闭底层连接池

    def __enter__(self): return self                                                   # 进入 with 语句时返回自身
    def __exit__(self, *args): self.close()                                            # 退出 with 语句时自动关闭
    
    
if __name__ == "__main__":
    with TempMail() as mail:
        print(mail.generateEmail())
        print(mail.listAll())