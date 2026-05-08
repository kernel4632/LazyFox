"""
这个文件负责把注册主流程串起来。

可直接调用的方法：
- runRegisterFlow(accountCount=1)        # 连续注册多个账号
- registerOneAccount()                   # 注册单个账号并返回结果字典

实现约束：
- 身份数据统一来自 tools/identity.py
- 浏览器动作统一来自 tools/browser.py
- 临时邮箱统一来自 TempMail/gptmail.py
"""

import sys
from pathlib import Path

# 获取当前文件的绝对路径
current_file_path = Path(__file__).resolve()
# 获取项目根目录 (也就是 LazyFox 文件夹)
# deepseek.py 在 register 文件夹下，所以 .parent 取到 register，再 .parent 取到 LazyFox
project_root = current_file_path.parent.parent

# 将根目录加入 sys.path 的第一位，确保优先搜索这里
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


# from pathlib import Path
import json
import re
import time

from TempMail.m2u import TempMail
from tools.browser import Browser
from tools.identity import Identity
from tools.log import Log

SIGN_UP_URL = "https://chat.deepseek.com/sign_up"
REGISTER_API_URL = "https://chat.deepseek.com/api/v0/users/register"
ACCOUNTS_FILE = Path("accounts.json")

EMAIL_INPUT = [
    "div.ds-form-item:nth-child(2) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero div.ds-form-item.ds-form-item--none.ds-form-item--label-m div.ds-form-item__content div.ds-input.ds-input--none.ds-input--bordered.ds-input--l input.ds-input__input",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[2]/div[1]/div/input",
]

PASSWORD_INPUT = [
    "div.ds-form-item:nth-child(3) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero div.ds-form-item.ds-form-item--none.ds-form-item--label-m div.ds-form-item__content div.ds-input.ds-input--none.ds-input--bordered.ds-input--l input.ds-input__input",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[3]/div[1]/div/input",
]

CONFIRM_PASSWORD_INPUT = [
    "div.ds-form-item:nth-child(4) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero div.ds-form-item.ds-form-item--none.ds-form-item--label-m div.ds-form-item__content div.ds-input.ds-input--none.ds-input--bordered.ds-input--l input.ds-input__input",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[4]/div[1]/div/input",
]

SEND_MAIL_BUTTON = [
    "button.ds-link-button:nth-child(2) > span:nth-child(1)",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero div.ds-form-item.ds-form-item--none.ds-form-item--label-m.ds-verify-code-input-form-item div.ds-form-item__content div.ds-input.ds-input--none.ds-input--bordered.ds-input--l div.ds-input__suffix button.ds-link-button.ds-verify-code-input-countdown span.ds-link-button__text",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[5]/div[1]/div/div/button/span",
]

VERIFY_CODE_INPUT = [
    "div.ds-form-item:nth-child(5) > div:nth-child(1) > div:nth-child(1) > input:nth-child(1)",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero div.ds-form-item.ds-form-item--none.ds-form-item--label-m.ds-verify-code-input-form-item div.ds-form-item__content div.ds-input.ds-input--none.ds-input--bordered.ds-input--l input.ds-input__input",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/div[5]/div[1]/div/input",
]

SIGN_UP_BUTTON = [
    ".ds-atom-button",
    "html.notranslate body.zh_HK.dark div#root div.ds-theme div.c994dda2 div._99ad066 div.ds-theme div.ds-auth-form-wrapper.ds-sign-up-form-wrapper div.ds-sign-up-form__main div.ds-auth-form__main-hero button.ds-atom-button.ds-basic-button.ds-basic-button--primary",
    "/html/body/div[1]/div/div/div[2]/div[1]/div/div[2]/div/button",
]

SUCCESS_SELECTOR = "#root > div > div > div.c3ecdb44 > div.dc04ec1d > div > div._3586175.ds-scroll-area > div._6d215eb.f27d1011.ds-scroll-area > div.fd90d2b2 > svg"

logger = Log(name="deepseek-register", resetHandlers=True)


class DeepSeekRegister:
    def __init__(self):
        self.identity = Identity(locale="en")

    def makeAccountData(self):
        logger.info("正在生成账号身份数据。")
        profile = self.identity.profile(locale="en")
        accountData = {
            "profile": profile,
            "email": "",
            "password": profile["password"],
            "token": "",
        }
        logger.info("账号身份数据生成完成。")
        return accountData

    def bindRegisterToken(self, browser, accountData):
        logger.info("正在绑定注册响应监听。")
        page = browser.getPage()

        def handleRegisterResponse(response):
            if response.url != REGISTER_API_URL:
                return

            try:
                body = response.json()
                token = str(body["data"]["biz_data"]["user"].get("token", ""))
                accountData["token"] = token
                logger.info(f"注册响应已捕获，token 是否存在: {bool(token)}")
            except Exception as error:
                logger.warning(f"读取注册响应失败: {error}")

        page.on("response", handleRegisterResponse)
        logger.info("注册响应监听绑定完成。")

    def checkPhoneNumberMode(self, browser):
        logger.info("正在检查页面是否误切到手机号注册模式。")
        page = browser.getPage()

        if browser.show(EMAIL_INPUT, timeout=1500):
            logger.info("当前页面仍然是邮箱注册模式。")
            return

        phoneInput = page.locator('input[placeholder="Phone number"]').first

        try:
            isPhoneMode = phoneInput.is_visible(timeout=1500)
        except Exception:
            isPhoneMode = False

        if not isPhoneMode:
            logger.info("当前页面没有发现手机号输入框。")
            return

        message = "检测到注册页出现 placeholder='Phone number'，当前节点大概率被分流到手机号注册模式，建议立即更换代理节点后重试。"
        logger.error(message)
        raise SystemExit(message)

    def openRegisterPage(self, browser):
        logger.info("正在打开注册页面。")
        isOpened = browser.goto(
            SIGN_UP_URL,
            retryCount=2,
            retryInterval=1,
        )

        if not isOpened:
            raise RuntimeError("注册页面打开失败")

        browser.sleep(3)  # 等待页面 JavaScript 完全渲染注册表单

        self.checkPhoneNumberMode(browser)

        if not browser.show(EMAIL_INPUT, timeout=5000):
            raise RuntimeError("注册页面未找到邮箱输入框")

        logger.info("注册页面打开完成。")

    def makeTempEmail(self, mail):
        logger.info("正在生成临时邮箱。")
        email = mail.generateEmail()
        mail.getInbox()

        if not email:
            raise RuntimeError("临时邮箱生成失败")

        logger.info(f"临时邮箱生成完成: {email}")
        return email

    def fillRegisterForm(self, browser, accountData):
        logger.info("正在填写注册表单。")

        isEmailFilled = browser.fill(EMAIL_INPUT, accountData["email"], retryCount=1, retryInterval=1)
        if not isEmailFilled:
            raise RuntimeError("邮箱填写失败")

        isPasswordFilled = browser.fill(PASSWORD_INPUT, accountData["password"], retryCount=1, retryInterval=1)
        if not isPasswordFilled:
            raise RuntimeError("密码填写失败")

        isConfirmFilled = browser.fill(CONFIRM_PASSWORD_INPUT, accountData["password"], retryCount=1, retryInterval=1)
        if not isConfirmFilled:
            raise RuntimeError("确认密码填写失败")

        logger.info("注册表单填写完成。")

    def sendVerifyCode(self, browser):
        logger.info("正在点击发送验证码按钮。")
        isClicked = browser.click(SEND_MAIL_BUTTON, retryCount=2, retryInterval=1)

        if not isClicked:
            raise RuntimeError("发送验证码按钮点击失败")

        logger.info("发送验证码按钮点击完成。")

    def extractVerifyCode(self, text):
        logger.info("正在从邮件内容中手动提取验证码。")

        if not text:
            logger.warning("邮件内容为空，无法提取验证码。")
            return ""

        codePatternList = [
            r"验证码[：:]\s*\n\s*(\d{6})",  # DeepSeek 中文格式：验证码：\n\n737945
            r"verification code[^\d]{0,40}(\d{6})",
            r"code below[^\d]{0,40}(\d{6})",
            r"DeepSeek[^\d]{0,80}(\d{6})",
            r">\s*(\d{6})\s*<",
            r"\b(\d{6})\b",
        ]

        for codePattern in codePatternList:
            match = re.search(codePattern, text, re.IGNORECASE | re.DOTALL)

            if not match:
                continue

            code = match.group(1)
            logger.info(f"验证码提取成功: {code}")
            return code

        logger.warning("邮件内容里没有提取到验证码。")
        return ""

    def readVerifyCodeFromMail(self, mailItem, mail):
        if not mailItem:
            return ""

        subjectText = str(mailItem.get("subject", ""))
        messageId = str(mailItem.get("mailID", mailItem.get("messageID", "")))  # 兼容 mailID 和 messageID 两种字段名
        bodyText = mail.readMessage(messageId)
        fullText = subjectText + "\n" + bodyText

        logger.info(f"正在检查邮件，主题: {subjectText}")
        logger.info(f"邮件正文长度: {len(bodyText)} 字符")
        return self.extractVerifyCode(fullText)

    def waitVerifyCode(self, mail):
        logger.info("正在等待邮箱验证码。")
        startTime = time.time()
        timeoutSeconds = 120
        pollSeconds = 2
        latestMailFingerprint = ""

        while time.time() - startTime < timeoutSeconds:
            latestMail = mail.getLatestMail()

            if latestMail:
                latestFingerprint = str(latestMail.get("mailID", latestMail.get("messageID", "")))

                if latestFingerprint != latestMailFingerprint:
                    latestMailFingerprint = latestFingerprint
                    code = self.readVerifyCodeFromMail(latestMail, mail)

                    if code:
                        logger.info(f"邮箱验证码获取完成: {code}")
                        return code

                    logger.error("最新邮件存在，但无法提取到验证码，停止本轮注册。")
                    raise RuntimeError(f"邮件主题: {latestMail.get('subject', '')}，但内容中未找到有效的验证码格式。")
                else:
                    logger.info("最新邮件和上一轮相同，继续等待新验证码邮件。")
            else:
                logger.info("暂时还没有读取到任何邮件，继续等待。")

            time.sleep(pollSeconds)

        raise RuntimeError("邮箱验证码获取失败，请检查临时邮箱服务、验证码投递状态或邮件内容格式。")

    def submitRegisterForm(self, browser, code):
        logger.info("正在填写验证码并提交注册。")

        isCodeFilled = browser.fill(VERIFY_CODE_INPUT, code, retryCount=1, retryInterval=1)
        if not isCodeFilled:
            raise RuntimeError("验证码填写失败")

        isSubmitted = browser.click(
            SIGN_UP_BUTTON,
            showSelector=SUCCESS_SELECTOR,
            retryCount=2,
            retryInterval=1,
        )
        if not isSubmitted:
            raise RuntimeError("注册提交失败")

        isSuccessShown = browser.wait(SUCCESS_SELECTOR, timeout=15000)
        if not isSuccessShown:
            raise RuntimeError("注册成功标记未出现")

        logger.info("注册提交流程完成。")

    def getDefaultAccountStore(self):
        logger.info("正在生成默认账号配置结构。")
        return {
            "keys": [],
            "accounts": [],
            "model_aliases": {
                "gpt-4o": "deepseek-chat",
                "gpt-5-codex": "deepseek-reasoner",
                "o3": "deepseek-reasoner",
            },
            "compat": {
                "wide_input_strict_output": True,
                "strip_reference_markers": True,
            },
            "responses": {
                "store_ttl_seconds": 900,
            },
            "embeddings": {
                "provider": "deterministic",
            },
            "claude_mapping": {
                "fast": "deepseek-chat",
                "slow": "deepseek-reasoner",
            },
            "admin": {
                "jwt_expire_hours": 24,
            },
            "runtime": {
                "account_max_inflight": 2,
                "account_max_queue": 0,
                "global_max_inflight": 0,
                "token_refresh_interval_hours": 6,
            },
            "auto_delete": {
                "mode": "none",
            },
        }

    def loadAccountStore(self):
        logger.info("正在读取已有账号配置文件。")

        if not ACCOUNTS_FILE.exists():
            logger.warning("账号配置文件不存在，改用默认结构。")
            return self.getDefaultAccountStore()

        try:
            with ACCOUNTS_FILE.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as error:
            logger.warning(f"读取账号配置文件失败，改用默认结构: {error}")
            return self.getDefaultAccountStore()

        if not isinstance(data, dict):
            logger.warning("账号配置文件格式不是对象，改用默认结构。")
            return self.getDefaultAccountStore()

        defaultData = self.getDefaultAccountStore()
        defaultData.update(data)

        if not isinstance(defaultData.get("accounts"), list):
            defaultData["accounts"] = []

        if not isinstance(defaultData.get("keys"), list):
            defaultData["keys"] = []

        logger.info("已有账号配置文件读取完成。")
        return defaultData

    def makeSavedAccount(self, accountData):
        logger.info("正在生成可保存的账号对象。")
        savedAccount = {
            "email": accountData["email"],
            "password": accountData["password"],
        }

        mobile = accountData["profile"].get("phone", "")
        if not savedAccount["email"] and mobile:
            savedAccount = {
                "mobile": mobile,
                "password": accountData["password"],
            }

        logger.info("可保存的账号对象生成完成。")
        return savedAccount

    def saveAccount(self, accountData):
        logger.info("正在保存账号结果。")
        storeData = self.loadAccountStore()
        savedAccount = self.makeSavedAccount(accountData)
        storeData["accounts"].append(savedAccount)

        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ACCOUNTS_FILE.open("w", encoding="utf-8") as file:
            json.dump(storeData, file, ensure_ascii=False, indent=2)

        logger.info("账号结果保存完成。")

    def registerOneAccount(self):
        logger.info("开始注册单个账号。")
        accountData = self.makeAccountData()
        browserProfile = accountData["profile"]["browser"]
        logger.info(f"本次浏览器资料: {browserProfile.get('browser', '')} {browserProfile.get('version', '')}")

        with TempMail() as mail:
            accountData["email"] = self.makeTempEmail(mail)

            with Browser(
                engine="camoufox",
                headless=False,
                os="windows",
                geoip=True,
                humanize=False,
                window=(1280, 720),
            ) as browser:
                self.bindRegisterToken(browser, accountData)
                self.openRegisterPage(browser)
                self.fillRegisterForm(browser, accountData)
                self.sendVerifyCode(browser)
                code = self.waitVerifyCode(mail)
                self.submitRegisterForm(browser, code)
                browser.sleep(2)

        self.saveAccount(accountData)
        logger.info("单个账号注册完成。")
        return accountData

    def runRegisterFlow(self, accountCount=1):
        logger.info(f"准备开始批量注册，总数: {accountCount}")
        resultList = []

        for index in range(accountCount):
            currentIndex = index + 1
            logger.info(f"开始注册 {currentIndex}/{accountCount}")

            try:
                accountData = self.registerOneAccount()
                resultList.append(accountData)
                logger.info(f"注册 {currentIndex}/{accountCount} 成功")
            except SystemExit:
                raise
            except Exception as error:
                logger.exception(f"注册 {currentIndex}/{accountCount} 失败: {error}")

        logger.info("批量注册流程结束。")
        return resultList


def registerOneAccount():
    register = DeepSeekRegister()
    return register.registerOneAccount()


def runRegisterFlow(accountCount=1):
    register = DeepSeekRegister()
    return register.runRegisterFlow(accountCount=accountCount)


if __name__ == "__main__":
    startTime = time.time()
    logger.info("deepseek 注册流程启动")
    runRegisterFlow(accountCount=1)
    endTime = time.time()
    duration = endTime - startTime
    logger.info(f"注册进程完成，耗时 {duration:.2f} 秒")
