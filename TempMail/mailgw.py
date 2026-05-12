import httpx
import re
import time
import json

"""
Mail.gw 临时邮箱模块

使用方法：
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

Mail.gw API 特性：
    - 完全免费，无需 API Key
    - 每个 IP 限制 8 QPS
    - 账号创建和 /domains 接口无需认证，其他接口均需 Bearer Token
    - 支持 SSE（Mercure）实时推送新邮件事件
    - 与 Mail.tm API 兼容，区别仅在 Base URL
"""


class TempMail:
    """临时邮箱模块（适配 Mail.gw）"""

    def __init__(self, apiUrl="https://api.mail.gw", password="lazFox123"):
        self.apiUrl = apiUrl
        self.password = password
        self.token = ""
        self.accountID = ""
        self.email = ""
        self.domains = []
        self.seenIds = set()
        self.baselineIds = set()
        self.mailMap = {}
        self.client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
            },
            timeout=30,
            follow_redirects=True,
        )

    def sendRequest(self, method, path, params=None, json=None):
        if self.token:
            headers = {"Authorization": f"Bearer {self.token}"}
        else:
            headers = {}
        response = self.client.request(
            method=method,
            url=self.apiUrl + path,
            params=params,
            json=json,
            headers=headers,
        )
        response.raise_for_status()
        return response

    def fetchDomains(self):
        response = self.sendRequest("GET", "/domains")
        data = response.json()
        members = data.get("hydra:member", [])
        self.domains = [d.get("domain", "") for d in members if d.get("domain")]
        return self.domains

    def fetchAccount(self, address, password):
        response = self.sendRequest(
            "POST",
            "/accounts",
            json={"address": address, "password": password},
        )
        return response.json()

    def fetchToken(self, address, password):
        response = self.sendRequest(
            "POST",
            "/token",
            json={"address": address, "password": password},
        )
        data = response.json()
        self.token = data.get("token", "")
        return self.token

    def fetchMessages(self, page=1):
        response = self.sendRequest("GET", "/messages", params={"page": page})
        data = response.json()
        totalItems = data.get("hydra:totalItems", 0)
        members = data.get("hydra:member", [])
        return members, totalItems

    def fetchMessage(self, messageId):
        response = self.sendRequest("GET", f"/messages/{messageId}")
        return response.json()

    def markAsRead(self, messageId):
        response = self.sendRequest("PATCH", f"/messages/{messageId}")
        return response.json()

    def deleteMessage(self, messageId):
        self.sendRequest("DELETE", f"/messages/{messageId}")
        return True

    def findCode(self, text):
        match = re.search(r"\b\d{6}\b", text)
        if not match:
            return None
        return match.group(0)

    def findLink(self, text, keyword=""):
        allLinks = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text or "")
        if keyword:
            allLinks = [x for x in allLinks if keyword in x]
        if not allLinks:
            return None
        return allLinks[0]

    def normalizeMessage(self, item):
        if not isinstance(item, dict):
            return None
        msgid = str(item.get("id", ""))
        subject = str(item.get("subject", ""))
        sender = str(item.get("from", {}).get("address", ""))
        date = str(item.get("createdAt", ""))
        intro = str(item.get("intro", ""))
        text = str(item.get("text", ""))
        body = text if text else intro
        normalized = {
            "messageID": msgid,
            "subject": subject,
            "from": sender,
            "date": date,
            "body": body,
        }
        return normalized

    def streamOneEvent(self, timeoutSeconds=60):
        if not self.token or not self.accountID:
            return None
        mercureUrl = "https://api.mail.gw/.well-known/mercure"
        topic = f"/accounts/{self.accountID}"
        params = {"topic": topic, "Authorization": f"Bearer {self.token}"}
        startTime = time.time()
        currentEvent = ""
        currentDataLines = []
        with self.client.stream(
            "GET",
            mercureUrl,
            params=params,
            headers={
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Authorization": f"Bearer {self.token}",
            },
            timeout=httpx.Timeout(connect=10, read=timeoutSeconds + 5, write=10, pool=10),
        ) as response:
            response.raise_for_status()
            for rawLine in response.iter_lines():
                if time.time() - startTime >= timeoutSeconds:
                    break
                if rawLine is None:
                    continue
                line = rawLine.strip()
                if not line:
                    if not currentEvent and not currentDataLines:
                        continue
                    rawData = "\n".join(currentDataLines)
                    eventName = currentEvent
                    currentEvent = ""
                    currentDataLines = []
                    if eventName == "connected":
                        continue
                    if eventName == "message":
                        try:
                            payload = json.loads(rawData)
                        except Exception:
                            continue
                        messageId = str(payload.get("id", ""))
                        if not messageId:
                            continue
                        return payload
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    currentEvent = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    currentDataLines.append(line[5:].strip())
        return None

    def generateEmail(self):
        if not self.domains:
            self.fetchDomains()
        if not self.domains:
            raise RuntimeError("fetchDomains failed, no domains available")
        import random

        username = f"lazy{random.randint(100000, 999999)}"
        self.email = f"{username}@{self.domains[0]}"
        self.fetchAccount(self.email, self.password)
        self.fetchToken(self.email, self.password)
        me = self.sendRequest("GET", "/me").json()
        self.accountID = str(me.get("id", ""))
        self.seenIds = set()
        self.baselineIds = set()
        self.mailMap = {}
        return self.email

    def getInbox(self):
        if not self.email:
            return []
        try:
            messageList, _ = self.fetchMessages(page=1)
        except Exception:
            return []
        normalizedList = []
        for item in messageList:
            mail = self.normalizeMessage(item)
            if not mail:
                continue
            msgid = mail.get("messageID", "")
            if msgid:
                self.mailMap[msgid] = mail
            normalizedList.append(mail)
        if not self.baselineIds:
            for item in normalizedList:
                msgid = item.get("messageID", "")
                if msgid:
                    self.baselineIds.add(msgid)
            return []
        skipIds = self.seenIds | self.baselineIds
        freshList = []
        for item in normalizedList:
            msgid = item.get("messageID", "")
            if not msgid:
                continue
            if msgid in skipIds:
                continue
            freshList.append(item)
        return freshList

    def readMessage(self, messageId):
        if not messageId:
            return ""
        cached = self.mailMap.get(str(messageId), {})
        if cached.get("body"):
            self.seenIds.add(str(messageId))
            return str(cached.get("body", ""))
        detail = self.fetchMessage(str(messageId))
        mail = self.normalizeMessage(detail)
        if not mail:
            return ""
        self.mailMap[str(messageId)] = mail
        self.seenIds.add(str(messageId))
        self.markAsRead(str(messageId))
        return str(mail.get("body", ""))

    def waitNewMail(self, timeoutSeconds=60, pollSeconds=2):
        freshList = self.getInbox()
        if freshList:
            return freshList[0]
        startTime = time.time()
        while time.time() - startTime < timeoutSeconds:
            remainSeconds = max(1, int(timeoutSeconds - (time.time() - startTime)))
            event = self.streamOneEvent(timeoutSeconds=remainSeconds)
            if not event:
                time.sleep(min(pollSeconds, 1))
                continue
            messageId = str(event.get("id", ""))
            if not messageId:
                time.sleep(min(pollSeconds, 1))
                continue
            try:
                detail = self.fetchMessage(messageId)
            except Exception:
                time.sleep(min(pollSeconds, 1))
                continue
            mail = self.normalizeMessage(detail)
            if not mail:
                time.sleep(min(pollSeconds, 1))
                continue
            if messageId not in self.seenIds:
                self.mailMap[messageId] = mail
                return mail
            time.sleep(min(pollSeconds, 1))
        return None

    def getCode(self, timeoutSeconds=60, pollSeconds=2):
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)
        if not mail:
            return None
        body = self.readMessage(mail.get("messageID", ""))
        subject = str(mail.get("subject", ""))
        code = self.findCode(subject)
        if code:
            return code
        code = self.findCode(body)
        if code:
            return code
        return self.findCode(subject + "\n" + body)

    def getLink(self, keyword="", timeoutSeconds=60, pollSeconds=2):
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)
        if not mail:
            return None
        body = self.readMessage(mail.get("messageID", ""))
        link = self.findLink(body, keyword)
        if link:
            return link
        subject = str(mail.get("subject", ""))
        return self.findLink(subject, keyword)

    def listAll(self):
        if not self.email:
            return []
        try:
            messageList, totalItems = self.fetchMessages(page=1)
        except Exception:
            return []
        allMails = list(messageList)
        if totalItems > 30:
            for page in range(2, (totalItems // 30) + 2):
                try:
                    more, _ = self.fetchMessages(page=page)
                    allMails.extend(more)
                except Exception:
                    break
        normalizedList = []
        for item in allMails:
            mail = self.normalizeMessage(item)
            if not mail:
                continue
            msgid = mail.get("messageID", "")
            if msgid:
                self.mailMap[msgid] = mail
            normalizedList.append(mail)
        return normalizedList

    def getLatestMail(self):
        allMailList = self.listAll()
        if not allMailList:
            return None
        return allMailList[0]

    def clearMarks(self):
        self.seenIds = set()
        self.baselineIds = set()

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    with TempMail() as mail:
        print(mail.generateEmail())
        print(mail.listAll())
