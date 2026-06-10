"""
Browser 类 - 基于 nodriver 的智能浏览器自动化工具

这是一个统一的浏览器自动化入口，封装了 nodriver 浏览器引擎，
提供更智能、更适合业务直接调用的 API，同时具备更强的反检测能力。

【核心优势】
- 基于 nodriver，无需额外 WebDriver，直接与 Chrome/Chromium 通信
- 内置强大的伪装能力，自动处理指纹、UA、语言等检测点
- 简化的 API 设计，易于使用和理解

【基本用法】
python
from browser import Browser

# 方式1：使用 with 语句（推荐）
with Browser(headless=False) as browser:
    browser.goto("https://example.com")
    browser.click("#button")
    # 使用完毕自动关闭

# 方式2：手动管理
browser = Browser()
try:
    browser.goto("https://example.com")
    browser.fill("#input", "text")
finally:
    browser.close()


【配置选项】
BrowserConfig 类提供了以下可配置项：
- clickRetryCount: 点击动作默认重试次数（默认3）
- actionRetryCount: 非点击动作默认重试次数（默认2）
- retryInterval: 重试间隔秒数（默认1.0）
- actionTimeout: 普通动作超时毫秒（默认8000）
- resultTimeout: 动作后等待结果超时毫秒（默认2500）
- gotoTimeout: 打开页面超时毫秒（默认30000）
- waitTimeout: wait方法默认超时毫秒（默认10000）
- typeDelay: 逐字输入间隔毫秒（默认80）
- isDebug: 是否打印调试日志（默认True）
- headless: 是否无头运行（默认False）
- viewportWidth: 视口宽度（默认1440）
- viewportHeight: 视口高度（默认900）
- userAgent: 自定义UA字符串（默认空，自动生成真实UA）

【主要方法】
1. 生命周期管理
   - start(): 启动浏览器
   - close(): 关闭浏览器
   - getPage(): 获取当前页面对象

2. 页面导航
   - goto(url, **kwargs): 打开URL
   - reload(**kwargs): 刷新页面
   - back(**kwargs): 后退
   - forward(**kwargs): 前进

3. 元素操作
   - click(selector, **kwargs): 点击元素
   - fill(selector, value, **kwargs): 填写输入框
   - type(selector, value, **kwargs): 逐字输入
   - press(selector, key, **kwargs): 按键
   - check(selector, **kwargs): 勾选复选框
   - uncheck(selector, **kwargs): 取消勾选
   - select(selector, value, **kwargs): 下拉选择
   - hover(selector, **kwargs): 鼠标悬停
   - dblclick(selector, **kwargs): 双击
   - focus(selector, **kwargs): 聚焦元素
   - blur(selector, **kwargs): 失焦
   - scroll(selector=None, position=None): 滚动页面
   - remove(selector, **kwargs): 移除元素

4. 元素查询
   - has(selector, **kwargs): 判断元素是否存在
   - show(selector, **kwargs): 判断元素是否可见
   - wait(selector, **kwargs): 等待元素满足条件
   - find(selector, **kwargs): 查找元素
   - count(selector): 统计元素数量
   - getText(selector, **kwargs): 获取文本
   - getValue(selector, **kwargs): 获取输入框值
   - getHtml(selector, **kwargs): 获取HTML
   - isChecked(selector, **kwargs): 判断是否勾选
   - isDisabled(selector, **kwargs): 判断是否禁用

5. 智能操作（支持自动重试和结果验证）
   所有操作方法都支持以下智能参数：
   - showSelector: 等待指定元素出现
   - hideSelector: 等待指定元素消失
   - urlContains: 等待URL包含指定文本
   - textContains: 等待页面包含指定文本
   - valueIs: 等待输入框值等于指定值
   - countIs: 等待元素数量等于指定值
   - countAtLeast: 等待元素数量至少达到指定值
   - titleContains: 等待页面标题包含指定文本
   - retryCount: 自定义重试次数
   - retryInterval: 自定义重试间隔

6. 工具方法
   - screenshot(path=None, **kwargs): 截图
   - evaluate(script, arg=None): 执行JS脚本
   - getTitle(): 获取页面标题
   - getUrl(): 获取当前URL
   - sleep(seconds): 等待指定秒数
   - log(message): 打印日志

【反检测特性】
- 自动隐藏 navigator.webdriver 属性
- 自动设置真实的语言和时区
- 模拟真实的硬件信息（内存、CPU核心数）
- 动态生成逼真的用户代理字符串
- 设置正确的平台信息

【开发者提示】
- 所有方法都有完善的日志输出，开启 isDebug=True 可查看详细执行过程
- 智能重试机制会自动处理网络波动和元素加载延迟
- 建议使用 with 语句管理浏览器生命周期，确保资源正确释放
- 复杂操作建议使用智能参数进行结果验证，提高稳定性
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple, Callable, Union
import time
import random
import asyncio

from nodriver import Tab, start, Element


@dataclass
class BrowserConfig:
    """浏览器配置类"""

    clickRetryCount: int = 3  # 点击动作默认重试次数
    actionRetryCount: int = 2  # 非点击动作默认重试次数
    retryInterval: float = 1.0  # 重试间隔秒数
    actionTimeout: int = 8000  # 普通动作超时毫秒
    resultTimeout: int = 2500  # 动作后等待结果超时毫秒
    gotoTimeout: int = 30000  # 打开页面超时毫秒
    waitTimeout: int = 10000  # wait方法默认超时毫秒
    typeDelay: int = 80  # 逐字输入间隔毫秒
    isDebug: bool = True  # 是否打印调试日志
    headless: bool = False  # 是否无头运行
    viewportWidth: int = 1440  # 默认视口宽度
    viewportHeight: int = 900  # 默认视口高度
    userAgent: str = ""  # 自定义UA，空则自动生成


class Browser:
    def __init__(
        self,
        page=None,
        config: Optional[BrowserConfig] = None,
        autoStart: bool = True,
        **browserArgs,
    ):
        print("正在初始化 Browser。")

        self.config = config or BrowserConfig()
        self.browserArgs = browserArgs

        self.headless = self.browserArgs.pop("headless", self.config.headless)
        self.viewportWidth = self.browserArgs.pop("viewportWidth", self.config.viewportWidth)
        self.viewportHeight = self.browserArgs.pop("viewportHeight", self.config.viewportHeight)
        self.userAgent = self.browserArgs.pop("userAgent", self.config.userAgent)

        self.tab = page
        self.isStarted = page is not None
        self.browser = None
        self._loop = None

        if autoStart and self.tab is None:
            self.start()

        print("Browser 初始化完成。")

    def __enter__(self):
        if not self.isStarted or self.tab is None:
            self.start()
        return self

    def __exit__(self, excType, excValue, traceback):
        self.close()

    def _run_async(self, coroutine):
        """在共享事件循环中运行异步操作"""
        try:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop.run_until_complete(coroutine)
            else:
                return asyncio.run(coroutine)
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                self._loop = asyncio.new_event_loop()
                return self._loop.run_until_complete(coroutine)
            raise

    def start(self):
        if self.isStarted and self.tab is not None:
            self.log("Browser 已经启动，跳过重复启动。")
            return self

        self.log("正在启动 nodriver 浏览器。")

        try:
            self._loop = asyncio.new_event_loop()
            self.browser = self._loop.run_until_complete(start(headless=self.headless, user_agent=self.userAgent or self._generateRealisticUA(), viewport={"width": self.viewportWidth, "height": self.viewportHeight}, **self.browserArgs))

            # 使用 tabs 列表获取主页面，而不是有 bug 的 main_tab 属性
            if self.browser.tabs and len(self.browser.tabs) > 0:
                self.tab = self.browser.tabs[0]
            else:
                raise RuntimeError("无法获取主页面标签")

            self.isStarted = True

            self._applyAntiDetection()
            self.log("nodriver 浏览器启动完成。")
        except Exception as error:
            self.log(f"浏览器启动失败：{error}")
            raise

        return self

    def _generateRealisticUA(self):
        """生成逼真的 Chrome UA 字符串"""
        versions = [
            "120.0.6099.109",
            "121.0.6167.139",
            "122.0.6261.112",
            "123.0.6312.106",
            "124.0.6367.60",
        ]
        osInfo = [
            "(Windows NT 10.0; Win64; x64)",
            "(Windows NT 10.0; WOW64)",
            "(Windows NT 11.0; Win64; x64)",
        ]
        version = random.choice(versions)
        os = random.choice(osInfo)
        return f"Mozilla/5.0 {os} AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"

    def _applyAntiDetection(self):
        """应用反检测措施"""
        self.log("正在应用反检测配置...")

        self._run_async(self.tab.evaluate("Object.defineProperty(navigator, 'language', {get: () => 'zh-CN'});"))
        self._run_async(self.tab.evaluate("Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});"))
        self._run_async(self.tab.evaluate("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"))
        self._run_async(self.tab.evaluate("window.chrome = {runtime: {}};"))
        self._run_async(self.tab.evaluate("Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"))
        self._run_async(
            self.tab.evaluate("""
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        """)
        )

        self.log("反检测配置应用完成。")

    def close(self):
        """关闭浏览器

        使用 nodriver 内置的 stop() 方法关闭，它已经处理了异步关闭和进程终止的兜底。
        不要用 _run_async 包装 aclose()，因为 aclose 内部关闭 websocket 会破坏事件循环导致 Event loop is closed。
        """
        if self.browser:
            try:
                self.browser.stop()
                self.log("浏览器已关闭。")
            except Exception as error:
                self.log(f"stop() 关闭出错，尝试直接终止进程：{error}")
                try:
                    if self.browser._process and self.browser._process.returncode is None:
                        self.browser._process.terminate()
                except Exception:
                    pass
        self.isStarted = False
        self.tab = None
        self.browser = None
        if self._loop:
            try:
                self._loop.close()
            except:
                pass
        self._loop = None

    def log(self, message: str):
        """打印日志"""
        if self.config.isDebug:
            print(f"[Browser] {message}")

    def sleep(self, seconds: float):
        """等待指定秒数"""
        self.log(f"等待 {seconds} 秒。")
        time.sleep(seconds)

    def ensurePage(self) -> Tab:
        """确保页面可用"""
        if self.tab is None:
            self.start()
        return self.tab

    def getPage(self):
        """获取当前页面对象"""
        return self.ensurePage()

    def getTitle(self) -> str:
        """获取页面标题"""
        page = self.ensurePage()
        # 使用 JavaScript 获取实时标题，因为 page.title 属性不会自动更新
        try:
            return self._run_async(page.evaluate("document.title")) or page.title
        except:
            return page.title

    def getUrl(self) -> str:
        """获取当前URL"""
        page = self.ensurePage()
        # 使用 JavaScript 获取实时URL，因为 page.url 属性不会自动更新
        try:
            return self._run_async(page.evaluate("document.URL")) or page.url
        except:
            return page.url

    def normalizeSelector(self, selector: Any, timeout: Optional[int] = None) -> str:
        """标准化选择器"""
        if isinstance(selector, str):
            return selector
        if hasattr(selector, "__str__"):
            return str(selector)
        raise RuntimeError(f"无效 selector: {selector}")

    def getTimeout(self, timeout: Optional[int], default: int) -> int:
        """获取超时时间"""
        return timeout if timeout is not None else default

    def getRetryCount(self, retryCount: Optional[int], actionType: str) -> int:
        """获取重试次数"""
        if retryCount is not None:
            return retryCount
        return self.config.clickRetryCount if actionType == "click" else self.config.actionRetryCount

    def getRetryInterval(self, retryInterval: Optional[float]) -> float:
        """获取重试间隔"""
        return retryInterval if retryInterval is not None else self.config.retryInterval

    def hasSmartRule(
        self,
        showSelector: Any = None,
        hideSelector: Any = None,
        urlContains: str = None,
        textContains: str = None,
        titleContains: str = None,
        valueIs: str = None,
        countIs: int = None,
        countAtLeast: int = None,
        retryCount: int = None,
        retryInterval: float = None,
    ) -> bool:
        """判断是否有智能规则"""
        return any([showSelector, hideSelector, urlContains, textContains, titleContains, valueIs, countIs, countAtLeast, retryCount is not None, retryInterval is not None])

    def wait(
        self,
        selector: Any,
        state: str = "visible",
        timeout: Optional[int] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        textContains: Optional[str] = None,
    ) -> bool:
        """等待元素满足条件"""
        timeout = self.getTimeout(timeout, self.config.waitTimeout)
        endTime = time.time() + timeout / 1000
        selector = self.normalizeSelector(selector, timeout=timeout)

        self.log(f"正在等待元素状态：{selector}")

        while time.time() < endTime:
            if countIs is not None:
                if self.count(selector) == countIs:
                    return True
            elif countAtLeast is not None:
                if self.count(selector) >= countAtLeast:
                    return True
            elif textContains is not None:
                text = self.getText(selector, timeout=300)
                if text and textContains in text:
                    return True
            elif self.has(selector, state=state, timeout=300):
                return True
            time.sleep(0.1)

        self.log("wait 超时。")
        return False

    def has(self, selector: Any, state: str = "attached", timeout: Optional[int] = None) -> bool:
        """判断元素是否存在"""
        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        try:
            element = self.getLocator(selector, timeout=timeout)
            if element:
                if state == "visible":
                    return self._run_async(element.is_visible(timeout=timeout / 1000))
                return True
            return False
        except Exception:
            return False

    def show(self, selector: Any, timeout: Optional[int] = None) -> bool:
        return self.has(selector, state="visible", timeout=timeout)

    def count(self, selector: Any) -> int:
        """统计元素数量"""
        if not selector:
            self.log("count 失败：selector 为空。")
            return 0

        page = self.ensurePage()
        selector = self.normalizeSelector(selector, timeout=300)

        try:
            elements = self._run_async(page.select_all(selector, timeout=0.3))
            return len(elements) if elements else 0
        except Exception:
            return 0

    def getText(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        defaultValue: str = "",
        isStrip: bool = True,
    ) -> str:
        """获取元素文本"""
        if not selector:
            self.log("getText 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, timeout=timeout):
            return defaultValue

        try:
            element = self.getLocator(selector, timeout=timeout)
            text = element.text if element else ""
            return text.strip() if isStrip else text
        except Exception:
            return defaultValue

    def getValue(self, selector: Any, timeout: Optional[int] = None, defaultValue: str = "") -> str:
        """获取输入框值"""
        if not selector:
            self.log("getValue 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, timeout=timeout):
            return defaultValue

        try:
            element = self.getLocator(selector, timeout=timeout)
            return self._run_async(element.get_attribute("value")) if element else defaultValue
        except Exception:
            return defaultValue

    def getHtml(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        defaultValue: str = "",
        isOuter: bool = False,
    ) -> str:
        """获取元素HTML"""
        if not selector:
            self.log("getHtml 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, state="attached", timeout=timeout):
            return defaultValue

        try:
            element = self.getLocator(selector, timeout=timeout)
            return self._run_async(element.get_html()) if element else defaultValue
        except Exception:
            return defaultValue

    def isChecked(self, selector: Any, timeout: Optional[int] = None) -> bool:
        """判断复选框是否勾选"""
        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, timeout=timeout):
            return False

        try:
            element = self.getLocator(selector, timeout=timeout)
            return self._run_async(element.is_checked()) if element else False
        except Exception:
            return False

    def isDisabled(self, selector: Any, timeout: Optional[int] = None) -> bool:
        """判断元素是否禁用"""
        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, timeout=timeout):
            return False

        try:
            element = self.getLocator(selector, timeout=timeout)
            return self._run_async(element.get_attribute("disabled")) is not None if element else False
        except Exception:
            return False

    def find(self, selector: Any, timeout: Optional[int] = None):
        """查找元素"""
        if not selector:
            self.log("find 失败：selector 为空。")
            return None

        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, state="attached", timeout=timeout):
            self.log(f"find 失败：元素未出现 -> {selector}")
            return None

        try:
            return self.getLocator(selector, timeout=timeout)
        except Exception as error:
            self.log(f"find 失败：{error}")
            return None

    def getLocator(self, selector: Any, timeout: Optional[int] = None):
        """获取元素定位器"""
        page = self.ensurePage()
        selector = self.normalizeSelector(selector, timeout=timeout)
        return self._run_async(page.select_one(selector, timeout=timeout / 1000))

    def isPageReady(self) -> bool:
        """检查页面是否就绪"""
        page = self.ensurePage()
        try:
            state = self._run_async(page.evaluate("document.readyState"))
            return state in ["interactive", "complete"]
        except Exception:
            return False

    def waitPageReady(self, timeout: Optional[int] = None) -> bool:
        """等待页面进入可操作状态"""
        timeout = self.getTimeout(timeout, self.config.waitTimeout)
        endTime = time.time() + timeout / 1000

        self.log("正在等待页面进入可操作状态。")

        while time.time() < endTime:
            if self.isPageReady():
                return True
            time.sleep(0.2)
        return False

    def openPage(self, url: Optional[str] = None, showSelector: Optional[Any] = None) -> bool:
        """打开新页面"""
        if url:
            return self.goto(url, showSelector=showSelector)
        self.log(f"当前 page 是否可用：{self.tab is not None}")
        return self.tab is not None

    def goto(
        self,
        url: str,
        timeout: Optional[int] = None,
        waitUntil: str = "load",
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
        **kwargs,
    ) -> bool:
        """打开URL"""
        if not url:
            self.log("goto 失败：url 为空。")
            return False

        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在打开页面：{url}")

        def action():
            self._run_async(page.get(url))
            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

        if not isSmart:
            try:
                action()
                self.log("页面打开完成。")
                return True
            except Exception as error:
                self.log(f"goto 失败：{error}")
                return False

        return self.runAction(
            actionName="goto",
            actionFunc=action,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains or url,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def reload(
        self,
        timeout: Optional[int] = None,
        waitUntil: str = "domcontentloaded",
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """刷新页面"""
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log("正在刷新页面。")

        def action():
            try:
                self._run_async(page.reload())
            except Exception as error:
                self.log(f"reload 底层调用报错，但继续检查页面状态：{error}")
            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

        if not isSmart:
            try:
                action()
                self.log("页面刷新完成。")
                return True
            except Exception as error:
                self.log(f"reload 失败：{error}")
                return False

        return self.runAction(
            actionName="reload",
            actionFunc=action,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def back(
        self,
        timeout: Optional[int] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """后退"""
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        retryCount = self.getRetryCount(retryCount, "back")
        retryInterval = self.getRetryInterval(retryInterval)

        self.log("正在执行后退。")

        for index in range(retryCount + 1):
            attempt = index + 1
            beforeUrl = page.url

            self.log(f"back 第 {attempt} 次尝试开始，当前地址：{beforeUrl}")

            try:
                self._run_async(page.go_back())
            except Exception as error:
                self.log(f"go_back 报错，尝试 history.back() 兜底：{error}")
                self._run_async(page.evaluate("history.back()"))

            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

            currentUrl = page.url
            if currentUrl != beforeUrl:
                self.log(f"后退成功，当前地址：{currentUrl}")
                return True

            if index < retryCount:
                self.log(f"back 第 {attempt} 次未成功，等待 {retryInterval} 秒后重试。")
                time.sleep(retryInterval)

        self.log("back 失败：无法后退。")
        return False

    def forward(
        self,
        timeout: Optional[int] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """前进"""
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        retryCount = self.getRetryCount(retryCount, "forward")
        retryInterval = self.getRetryInterval(retryInterval)

        self.log("正在执行前进。")

        for index in range(retryCount + 1):
            attempt = index + 1
            beforeUrl = page.url

            self.log(f"forward 第 {attempt} 次尝试开始，当前地址：{beforeUrl}")

            try:
                self._run_async(page.go_forward())
            except Exception as error:
                self.log(f"go_forward 报错，尝试 history.forward() 兜底：{error}")
                self._run_async(page.evaluate("history.forward()"))

            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

            currentUrl = page.url
            if currentUrl != beforeUrl:
                self.log(f"前进成功，当前地址：{currentUrl}")
                return True

            if index < retryCount:
                self.log(f"forward 第 {attempt} 次未成功，等待 {retryInterval} 秒后重试。")
                time.sleep(retryInterval)

        self.log("forward 失败：无法前进。")
        return False

    def tryClick(self, selector: Any, timeout: int, isForce: bool = False):
        """尝试点击元素"""
        element = self.getLocator(selector, timeout=timeout)
        if not element:
            raise RuntimeError("元素不存在")

        self._run_async(element.scroll_into_view())

        if isForce:
            self._run_async(element.click(force=True))
            return

        try:
            self._run_async(element.click())
            return
        except Exception:
            pass

        try:
            self._run_async(element.click(force=True))
            return
        except Exception:
            pass

        self.evaluate(f"document.querySelector('{selector}')?.click()")

    def click(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        button: str = "left",
        clickCount: int = 1,
        delay: Optional[int] = None,
        modifiers: Optional[list] = None,
        position: Optional[dict] = None,
        isForce: bool = False,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """点击元素"""
        if not selector:
            self.log("click 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在点击元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"click 失败：元素未出现 -> {selector}")
            return False

        def action():
            self.tryClick(selector, timeout=timeout, isForce=isForce)

        if not isSmart:
            try:
                action()
                self.log("点击完成。")
                return True
            except Exception as error:
                self.log(f"click 失败：{error}")
                return False

        return self.runAction(
            actionName="click",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def fill(
        self,
        selector: Any,
        value: str,
        timeout: Optional[int] = None,
        isClear: bool = True,
        valueIs: Optional[str] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """填写输入框"""
        if not selector:
            self.log("fill 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        expectedValue = value if valueIs is None else valueIs
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            valueIs=expectedValue if (valueIs is not None or showSelector or hideSelector or retryCount is not None or retryInterval is not None) else None,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在填写输入框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"fill 失败：输入框未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            if isClear:
                self._run_async(element.clear())
            self._run_async(element.send_keys(value))

        if not isSmart:
            try:
                action()
                self.log("填写完成。")
                return True
            except Exception as error:
                self.log(f"fill 失败：{error}")
                return False

        return self.runAction(
            actionName="fill",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            valueIs=expectedValue,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def press(
        self,
        selector: Any,
        key: str,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """按键"""
        if not selector:
            self.log("press 失败：selector 为空。")
            return False

        if not key:
            self.log("press 失败：key 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在按键：{key} -> {selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"press 失败：元素未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            self._run_async(element.focus())

            key_map = {
                "Enter": "\r",
                "Tab": "\t",
                "Escape": "\x1b",
                "Backspace": "\x08",
                "Delete": "\x7f",
                "ArrowUp": "\x1b[A",
                "ArrowDown": "\x1b[B",
                "ArrowLeft": "\x1b[D",
                "ArrowRight": "\x1b[C",
                "Home": "\x1b[H",
                "End": "\x1b[F",
                "PageUp": "\x1b[5~",
                "PageDown": "\x1b[6~",
                "F1": "\x1bOP",
                "F2": "\x1bOQ",
                "F3": "\x1bOR",
                "F4": "\x1bOS",
                "F5": "\x1b[15~",
                "F6": "\x1b[17~",
                "F7": "\x1b[18~",
                "F8": "\x1b[19~",
                "F9": "\x1b[20~",
                "F10": "\x1b[21~",
                "F11": "\x1b[23~",
                "F12": "\x1b[24~",
                "Shift": "\x1b[1;2A",
                "Control": "\x1b[1;5A",
                "Alt": "\x1b[1;3A",
                "Meta": "\x1b[1;9A",
            }

            nodriver_key = key_map.get(key, key)
            self._run_async(element.send_keys(nodriver_key))

        if not isSmart:
            try:
                action()
                self.log("按键完成。")
                return True
            except Exception as error:
                self.log(f"press 失败：{error}")
                return False

        return self.runAction(
            actionName="press",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def screenshot(
        self,
        path: Optional[str] = None,
        fullPage: bool = True,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        **kwargs,
    ) -> str:
        """截图"""
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.actionTimeout)

        if showSelector:
            self.wait(showSelector, timeout=timeout)

        if not path:
            path = f"browser-shot-{int(time.time())}.png"

        self.log(f"正在截图：{path}")

        try:
            self._run_async(page.screenshot(path=path, full_page=fullPage))
            self.log("截图完成。")
            return path
        except Exception as error:
            self.log(f"screenshot 失败：{error}")
            return ""

    def evaluate(self, script: str, arg: Any = None, defaultValue: Any = None) -> Any:
        """执行页面脚本"""
        if not script:
            self.log("evaluate 失败：script 为空。")
            return defaultValue

        page = self.ensurePage()
        self.log("正在执行页面脚本。")

        try:
            if arg is None:
                return self._run_async(page.evaluate(script))
            return self._run_async(page.evaluate(script, arg))
        except Exception as error:
            self.log(f"evaluate 失败：{error}")
            return defaultValue

    def dblclick(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """双击元素"""
        if not selector:
            self.log("dblclick 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在双击元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"dblclick 失败：元素未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            self._run_async(element.dblclick())

        if not isSmart:
            try:
                action()
                self.log("双击完成。")
                return True
            except Exception as error:
                self.log(f"dblclick 失败：{error}")
                return False

        return self.runAction(
            actionName="dblclick",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def hover(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """鼠标悬停"""
        if not selector:
            self.log("hover 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在悬停元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"hover 失败：元素未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            self._run_async(element.hover())

        if not isSmart:
            try:
                action()
                self.log("悬停完成。")
                return True
            except Exception as error:
                self.log(f"hover 失败：{error}")
                return False

        return self.runAction(
            actionName="hover",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def type(
        self,
        selector: Any,
        value: str,
        timeout: Optional[int] = None,
        delay: Optional[int] = None,
        isClear: bool = True,
        valueIs: Optional[str] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """逐字输入"""
        if not selector:
            self.log("type 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        delay = delay if delay is not None else self.config.typeDelay
        selector = self.normalizeSelector(selector, timeout=timeout)
        expectedValue = value if valueIs is None else valueIs
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            valueIs=expectedValue if (valueIs is not None or showSelector or retryCount is not None or retryInterval is not None) else None,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在逐字输入：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"type 失败：输入框未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            self._run_async(element.click())
            if isClear:
                self._run_async(element.clear())
            self._run_async(element.send_keys(value, delay=delay / 1000))

        if not isSmart:
            try:
                action()
                self.log("逐字输入完成。")
                return True
            except Exception as error:
                self.log(f"type 失败：{error}")
                return False

        return self.runAction(
            actionName="type",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            valueIs=expectedValue,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def check(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """勾选复选框"""
        if not selector:
            self.log("check 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在勾选复选框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"check 失败：元素未出现 -> {selector}")
            return False

        if self.isChecked(selector, timeout=timeout):
            self.log("复选框已经是勾选状态。")
            return True

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.check())
            if not self.isChecked(selector, timeout=timeout):
                raise RuntimeError("复选框勾选后状态仍未变为选中。")

        if not isSmart:
            try:
                action()
                self.log("勾选完成。")
                return True
            except Exception as error:
                self.log(f"check 失败：{error}")
                return False

        return self.runAction(
            actionName="check",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def uncheck(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        hideSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """取消勾选复选框"""
        if not selector:
            self.log("uncheck 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(hideSelector=hideSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在取消勾选复选框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"uncheck 失败：元素未出现 -> {selector}")
            return False

        if not self.isChecked(selector, timeout=timeout):
            self.log("复选框已经是未勾选状态。")
            return True

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.uncheck())
            if self.isChecked(selector, timeout=timeout):
                raise RuntimeError("复选框取消勾选后仍然是选中状态。")

        if not isSmart:
            try:
                action()
                self.log("取消勾选完成。")
                return True
            except Exception as error:
                self.log(f"uncheck 失败：{error}")
                return False

        return self.runAction(
            actionName="uncheck",
            actionFunc=action,
            selector=selector,
            hideSelector=hideSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def select(
        self,
        selector: Any,
        value: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """下拉选择"""
        if not selector:
            self.log("select 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在选择下拉项：{selector} -> {value}")

        if not self.has(selector, timeout=timeout):
            self.log(f"select 失败：元素未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.scroll_into_view())
            self._run_async(element.select(value))

        if not isSmart:
            try:
                action()
                self.log("下拉选择完成。")
                return True
            except Exception as error:
                self.log(f"select 失败：{error}")
                return False

        return self.runAction(
            actionName="select",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def focus(self, selector: Any, timeout: Optional[int] = None) -> bool:
        """聚焦元素"""
        if not selector:
            self.log("focus 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, timeout=timeout):
            self.log(f"focus 失败：元素未出现 -> {selector}")
            return False

        try:
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.focus())
            self.log(f"已聚焦元素：{selector}")
            return True
        except Exception as error:
            self.log(f"focus 失败：{error}")
            return False

    def blur(self, selector: Any, timeout: Optional[int] = None, showSelector: Optional[Any] = None) -> bool:
        """失焦元素"""
        if not selector:
            self.log("blur 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, timeout=timeout):
            self.log(f"blur 失败：元素未出现 -> {selector}")
            return False

        def action():
            element = self.getLocator(selector, timeout=timeout)
            self._run_async(element.blur())

        if not showSelector:
            try:
                action()
                self.log(f"已让元素失焦：{selector}")
                return True
            except Exception as error:
                self.log(f"blur 失败：{error}")
                return False

        return self.runAction(
            actionName="blur",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
        )

    def scroll(self, selector: Optional[Any] = None, position: Optional[str] = None) -> bool:
        """滚动页面"""
        page = self.ensurePage()
        self.log("正在执行滚动。")

        try:
            if selector:
                selector = self.normalizeSelector(selector, timeout=600)
                element = self.getLocator(selector, timeout=600)
                self._run_async(element.scroll_into_view())
                self.log(f"已滚动到元素位置：{selector}")
                return True

            if position == "top":
                self._run_async(page.evaluate("window.scrollTo(0, 0)"))
                self.log("已滚动到页面顶部。")
                return True

            if position == "bottom":
                self._run_async(page.evaluate("window.scrollTo(0, document.body.scrollHeight)"))
                self.log("已滚动到页面底部。")
                return True

            self._run_async(page.mouse.wheel(0, 800))
            self.log("已执行一次普通向下滚动。")
            return True
        except Exception as error:
            self.log(f"滚动失败：{error}")
            return False

    def remove(self, selector: Any, timeout: Optional[int] = None) -> bool:
        """移除元素"""
        if not selector:
            self.log("remove 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, state="attached", timeout=timeout):
            self.log(f"remove 跳过：元素本来就不存在 -> {selector}")
            return True

        self.log(f"正在从页面中移除元素：{selector}")

        try:
            self.evaluate(f"""
                const elements = document.querySelectorAll('{selector}');
                elements.forEach(el => el.remove());
            """)
            self.log("元素移除完成。")
            return True
        except Exception as error:
            self.log(f"remove 失败：{error}")
            return False

    def runAction(
        self,
        actionName: str,
        actionFunc: Callable,
        selector: Any = None,
        showSelector: Any = None,
        hideSelector: Any = None,
        urlContains: str = None,
        textContains: str = None,
        titleContains: str = None,
        valueIs: str = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        """执行带重试和验证的动作"""
        retryCount = self.getRetryCount(retryCount, actionName)
        retryInterval = self.getRetryInterval(retryInterval)

        for index in range(retryCount + 1):
            attempt = index + 1
            self.log(f"{actionName} 第 {attempt} 次尝试开始。")

            try:
                actionFunc()
            except Exception as error:
                self.log(f"{actionName} 第 {attempt} 次动作报错：{error}")
                if index < retryCount:
                    self.log(f"{actionName} 第 {attempt} 次未达到预期，等待 {retryInterval} 秒后重试。")
                    time.sleep(retryInterval)
                    continue
                else:
                    self.log(f"{actionName} 已达到最大尝试次数，但仍未成功。")
                    return False

            if self._checkActionResult(
                showSelector=showSelector,
                hideSelector=hideSelector,
                urlContains=urlContains,
                textContains=textContains,
                titleContains=titleContains,
                valueIs=valueIs,
                selector=selector,
            ):
                self.log(f"{actionName} 完成。")
                return True

            if index < retryCount:
                self.log(f"{actionName} 第 {attempt} 次未达到预期，等待 {retryInterval} 秒后重试。")
                time.sleep(retryInterval)

        self.log(f"{actionName} 已达到最大尝试次数，但仍未成功。")
        return False

    def _checkActionResult(
        self,
        showSelector: Any = None,
        hideSelector: Any = None,
        urlContains: str = None,
        textContains: str = None,
        titleContains: str = None,
        valueIs: str = None,
        selector: Any = None,
    ) -> bool:
        """检查动作执行结果"""
        checkItems = []

        if showSelector:
            checkItems.append(lambda: self.has(showSelector, state="visible", timeout=1000))
        if hideSelector:
            checkItems.append(lambda: not self.has(hideSelector, state="attached", timeout=1000))
        if urlContains:
            checkItems.append(lambda: urlContains in self.getUrl())
        if textContains:
            checkItems.append(lambda: textContains in self.getText("body", timeout=1000))
        if titleContains:
            checkItems.append(lambda: titleContains in self.getTitle())
        if valueIs and selector:
            checkItems.append(lambda: self.getValue(selector, timeout=1000) == valueIs)

        if not checkItems:
            return True

        for check in checkItems:
            try:
                if not check():
                    return False
            except Exception:
                return False

        return True

    def waitUrlChange(self, oldUrl: str, timeout: Optional[int] = None) -> bool:
        """等待URL变化"""
        timeout = self.getTimeout(timeout, self.config.waitTimeout)
        endTime = time.time() + timeout / 1000

        while time.time() < endTime:
            if self.getUrl() != oldUrl:
                return True
            time.sleep(0.2)
        return False
