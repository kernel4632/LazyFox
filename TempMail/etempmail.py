import httpx                                                                          # HTTP 客户端库用于发送网络请求
import re                                                                             # 正则表达式库用于从主题或正文中提取验证码和链接
import time                                                                           # 时间库用于轮询等待和超时控制
import hashlib                                                                        # 哈希库用于给邮件生成稳定指纹避免重复处理

"""
# 拿验证码
with TempMail() as mail:
    print(mail.generateEmail())                                                       # 生成临时邮箱地址
    mail.getInbox()                                                                   # 立刻建立基线，避免把旧邮件误当成新邮件
    # ... 去注册页面填这个邮箱，点发送验证码 ...
    print(mail.getCode())                                                             # 自动等待并提取验证码

# 拿激活链接
with TempMail() as mail:
    mail.generateEmail()                                                              # 先生成邮箱
    mail.getInbox()                                                                   # 建立基线
    print(mail.getLink(keyword="activate"))                                           # 自动等待并提取包含 activate 的链接

# 手动翻邮件
with TempMail() as mail:
    mail.generateEmail()                                                              # 先生成邮箱
    time.sleep(10)                                                                    # 等一会让邮件送达
    for item in mail.getInbox():                                                      # 获取新邮件列表
        body = mail.readMessage(item)                                                 # 直接读取该邮件的 body 字段
        print(mail.findCode(item.get("subject", "") + "\n" + body))                   # 从主题+正文中找验证码
        print(mail.findLink(body))                                                    # 从正文中找链接
"""

class TempMail:
    """临时邮箱模块（适配 etempmail.com）"""

    def __init__(self, apiUrl="https://etempmail.com"):
        self.apiUrl = apiUrl                                                          # 邮箱服务的根地址
        self.email = ""                                                               # 当前生成的临时邮箱地址
        self.emailId = ""                                                             # 服务端返回的邮箱编号 id
        self.creationTime = ""                                                        # 邮箱创建时间 creation_time
        self.recoverKey = ""                                                          # 恢复邮箱用的 recover_key
        self.seenIds = set()                                                          # 已经处理过的邮件指纹集合
        self.baselineIds = set()                                                      # 建立基线时已有邮件的指纹集合
        self.client = httpx.Client(                                                   # 创建浏览器模拟客户端
            headers={
                "User-Agent": "Mozilla/5.0 Chrome/145.0.0.0",                         # 伪装成 Chrome 浏览器
                "X-Requested-With": "XMLHttpRequest",                                 # 标记为页面内部发出的请求
                "Accept": "application/json, text/plain, */*",                        # 优先接受 JSON 响应
                "Referer": self.apiUrl + "/",                                         # 带上来源页提高兼容性
                "Origin": self.apiUrl,                                                # 带上源站信息提高兼容性
            },
            timeout=30,                                                               # 单次请求最多等 30 秒
            follow_redirects=True,                                                    # 自动跟随页面跳转
        )

    # ==================== 算子层 ====================

    def sendRequest(self, method, path, params=None, json=None, data=None):           # 发送请求到邮箱服务
        response = self.client.request(                                               # 发起 HTTP 请求
            method=method,                                                            # 请求方法如 GET / POST
            url=self.apiUrl + path,                                                   # 拼接完整请求地址
            params=params,                                                            # URL 查询参数
            json=json,                                                                # JSON 请求体
            data=data,                                                                # 表单请求体
        )
        response.raise_for_status()                                                   # 请求失败时直接抛错方便排查问题
        return response                                                               # 返回完整响应给调用方

    def fetchEmailAddress(self):                                                      # 请求服务端生成一个新的临时邮箱
        response = self.sendRequest("POST", "/getEmailAddress")                       # 按 F12 抓包结果使用 POST 调用生成邮箱接口
        data = response.json()                                                        # 把返回内容解析成字典

        self.emailId = str(data.get("id", ""))                                        # 保存服务端返回的邮箱编号
        self.email = str(data.get("address", ""))                                     # 保存生成出来的邮箱地址
        self.creationTime = str(data.get("creation_time", ""))                        # 保存邮箱创建时间
        self.recoverKey = str(data.get("recover_key", ""))                            # 保存邮箱恢复密钥

        return data                                                                   # 返回完整原始数据给上层

    def fetchInbox(self, email=None):                                                 # 拉取指定邮箱的收件箱列表
        targetEmail = email or self.email                                             # 优先使用传入邮箱，否则使用当前邮箱
        if not targetEmail: return []                                                 # 没有邮箱时直接返回空列表

        response = None                                                               # 先准备一个响应变量用于后续兼容尝试

        # --- 优先尝试最常见的 JSON 提交方式 ---
        try:
            response = self.sendRequest("POST", "/getInbox", json={"address": targetEmail})# 假设接口使用 JSON 且参数名为 address
            data = response.json()                                                    # 尝试解析响应 JSON
            if isinstance(data, list): return data                                    # 返回值是列表说明命中正确格式
            if isinstance(data, dict) and isinstance(data.get("data"), list):         # 兼容 data 包裹列表的格式
                return data.get("data", [])                                           # 返回 data 字段中的邮件列表
        except Exception:
            pass                                                                      # 第一种方式失败则继续尝试其他参数形式

        # --- 尝试 JSON + email 参数名 ---
        try:
            response = self.sendRequest("POST", "/getInbox", json={"email": targetEmail})# 假设接口参数名是 email
            data = response.json()                                                    # 解析响应 JSON
            if isinstance(data, list): return data                                    # 返回值是列表说明命中正确格式
            if isinstance(data, dict) and isinstance(data.get("data"), list):         # 兼容 data 包裹列表的格式
                return data.get("data", [])                                           # 返回 data 字段中的邮件列表
        except Exception:
            pass                                                                      # 第二种方式失败则继续尝试

        # --- 尝试表单提交 + address 参数名 ---
        try:
            response = self.sendRequest("POST", "/getInbox", data={"address": targetEmail})# 假设接口用 form-data 或 x-www-form-urlencoded
            data = response.json()                                                    # 解析响应 JSON
            if isinstance(data, list): return data                                    # 返回值是列表说明命中正确格式
            if isinstance(data, dict) and isinstance(data.get("data"), list):         # 兼容 data 包裹列表的格式
                return data.get("data", [])                                           # 返回 data 字段中的邮件列表
        except Exception:
            pass                                                                      # 第三种方式失败则继续尝试

        # --- 尝试表单提交 + email 参数名 ---
        try:
            response = self.sendRequest("POST", "/getInbox", data={"email": targetEmail})# 再尝试 form 方式的 email 参数名
            data = response.json()                                                    # 解析响应 JSON
            if isinstance(data, list): return data                                    # 返回值是列表说明命中正确格式
            if isinstance(data, dict) and isinstance(data.get("data"), list):         # 兼容 data 包裹列表的格式
                return data.get("data", [])                                           # 返回 data 字段中的邮件列表
        except Exception:
            pass                                                                      # 第四种方式失败则继续尝试

        return []                                                                     # 所有常见方式都失败时返回空列表

    def buildMailFingerprint(self, item):                                             # 为一封邮件构造稳定唯一的指纹
        if not isinstance(item, dict): return ""                                      # 格式不对时返回空字符串

        subject = str(item.get("subject", ""))                                        # 取主题
        sender = str(item.get("from", ""))                                            # 取发件人
        date = str(item.get("date", ""))                                              # 取时间
        body = str(item.get("body", ""))                                              # 取正文

        raw = "\n".join([subject, sender, date, body])                                # 把关键字段拼成原始文本
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()       # 用 SHA1 生成固定长度的邮件指纹
        return digest                                                                 # 返回该邮件的唯一指纹

    def findCode(self, text):                                                         # 从文本中找出 6 位数字验证码
        match = re.search(r"\b\d{6}\b", text)                                        # 匹配独立的 6 位连续数字
        if not match: return None                                                     # 没找到就返回空
        return match.group(0)                                                         # 找到了就返回这 6 位数字

    def findLink(self, text, keyword=""):                                             # 从文本中找出链接地址
        allLinks = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)                 # 提取所有 http/https 开头的链接
        if keyword: allLinks = [x for x in allLinks if keyword in x]                  # 有关键词时只保留包含它的链接
        if not allLinks: return None                                                  # 没找到就返回空
        return allLinks[0]                                                            # 返回第一个匹配的链接

    # ==================== 编排层 ====================

    def generateEmail(self):                                                          # 生成一个新的临时邮箱地址
        self.fetchEmailAddress()                                                      # 调用底层接口生成邮箱并写入对象状态

        self.seenIds = set()                                                          # 清空已读记录避免新邮箱被旧状态干扰
        self.baselineIds = set()                                                      # 清空基线记录让新邮箱从零开始

        return self.email                                                             # 返回邮箱地址

    def getInbox(self):                                                               # 获取当前未读的新邮件列表
        if not self.email: return []                                                  # 没有邮箱时直接返回空列表

        try:
            messageList = self.fetchInbox(self.email)                                 # 拉取当前邮箱的完整收件箱
        except Exception:
            return []                                                                 # 请求失败时返回空列表不中断主流程

        normalizedList = []                                                           # 用于收集格式规范后的邮件列表
        for item in messageList:                                                      # 遍历服务端返回的每一项
            if not isinstance(item, dict): continue                                   # 跳过格式异常的数据
            normalizedItem = {                                                        # 统一字段格式，避免后面到处判空
                "subject": str(item.get("subject", "")),                              # 标准化主题
                "from": str(item.get("from", "")),                                    # 标准化发件人
                "date": str(item.get("date", "")),                                    # 标准化时间
                "body": str(item.get("body", "")),                                    # 标准化正文
            }
            normalizedItem["mailID"] = self.buildMailFingerprint(normalizedItem)      # 给每封邮件附加一个本地生成的 mailID 指纹
            if not normalizedItem["mailID"]: continue                                 # 指纹为空说明该邮件不合法直接跳过
            normalizedList.append(normalizedItem)                                     # 收集规范后的邮件项

        if not self.baselineIds:                                                      # 基线为空说明这是第一次查询收件箱
            for item in normalizedList:                                               # 遍历当前所有已有邮件
                mailId = item.get("mailID", "")                                       # 取出本地生成的邮件指纹
                if mailId: self.baselineIds.add(mailId)                               # 记入基线集合用于排除历史邮件
            return []                                                                 # 第一次调用只建立基线不返回邮件

        skipIds = self.seenIds | self.baselineIds                                     # 合并已读和基线得到需要跳过的指纹集合
        freshList = []                                                                # 用于收集真正的新邮件
        for item in normalizedList:                                                   # 遍历规范后的邮件列表
            mailId = item.get("mailID", "")                                           # 取出邮件指纹
            if not mailId: continue                                                   # 没有指纹的跳过
            if mailId in skipIds: continue                                            # 历史邮件或已处理邮件都跳过
            freshList.append(item)                                                    # 剩下的就是新邮件

        return freshList                                                              # 返回新邮件列表

    def readMessage(self, mailItem):                                                  # 读取一封邮件的正文并标记已读
        if not isinstance(mailItem, dict): return ""                                  # 参数不是字典时直接返回空字符串
        mailId = str(mailItem.get("mailID", ""))                                      # 取出本地生成的邮件指纹
        if mailId: self.seenIds.add(mailId)                                           # 把这封邮件标记为已读避免重复处理
        return str(mailItem.get("body", ""))                                          # 直接返回邮件正文，因为接口已附带 body

    def waitNewMail(self, timeoutSeconds=60, pollSeconds=2):                          # 等待一封新邮件到达
        startTime = time.time()                                                       # 记录开始时间
        while time.time() - startTime < timeoutSeconds:                               # 在超时时间内循环检查
            freshList = self.getInbox()                                               # 检查当前有没有新邮件
            if freshList: return freshList[0]                                         # 一旦有新邮件就返回第一封
            time.sleep(pollSeconds)                                                   # 没有新邮件就等待几秒后继续轮询
        return None                                                                   # 超时了还没等到就返回空

    def getCode(self, timeoutSeconds=60, pollSeconds=2):                              # 等新邮件并自动提取验证码
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                          # 等一封新邮件
        if not mail: return None                                                      # 没等到就返回空

        body = self.readMessage(mail)                                                 # 读取这封邮件正文并标记已读
        subject = str(mail.get("subject", ""))                                        # 取出主题用于辅助提取验证码

        code = self.findCode(subject)                                                 # 先从主题中找验证码，因为很多站会把验证码写进主题
        if code: return code                                                          # 主题中找到了就直接返回

        code = self.findCode(body)                                                    # 主题中没找到再从正文中找验证码
        if code: return code                                                          # 正文中找到了就返回

        code = self.findCode(subject + "\n" + body)                                   # 最后把主题和正文拼起来统一搜索一次
        return code                                                                   # 返回最终提取结果

    def getLink(self, keyword="", timeoutSeconds=60, pollSeconds=2):                  # 等新邮件并自动提取链接
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                          # 等一封新邮件
        if not mail: return None                                                      # 没等到就返回空

        body = self.readMessage(mail)                                                 # 读取这封邮件正文并标记已读
        link = self.findLink(body, keyword)                                           # 从正文中找链接
        if link: return link                                                          # 找到了就直接返回

        subject = str(mail.get("subject", ""))                                        # 备用地从主题中取文本
        link = self.findLink(subject, keyword)                                        # 极少数情况下主题里也可能有链接
        return link                                                                   # 返回链接或空值

    def listAll(self):                                                                # 列出所有邮件包括已读的和基线旧邮件
        if not self.email: return []                                                  # 没有邮箱时返回空列表
        try:
            messageList = self.fetchInbox(self.email)                                 # 获取完整收件箱
        except Exception:
            return []                                                                 # 请求失败时返回空列表

        normalizedList = []                                                           # 用于收集格式规范后的邮件列表
        for item in messageList:                                                      # 遍历每一封邮件
            if not isinstance(item, dict): continue                                   # 跳过格式异常的数据
            normalizedItem = {                                                        # 统一邮件结构
                "subject": str(item.get("subject", "")),                              # 标准化主题
                "from": str(item.get("from", "")),                                    # 标准化发件人
                "date": str(item.get("date", "")),                                    # 标准化时间
                "body": str(item.get("body", "")),                                    # 标准化正文
            }
            normalizedItem["mailID"] = self.buildMailFingerprint(normalizedItem)      # 补上本地生成的邮件指纹
            normalizedList.append(normalizedItem)                                     # 收集起来统一返回

        return normalizedList                                                         # 返回完整邮件列表不做新旧过滤

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
