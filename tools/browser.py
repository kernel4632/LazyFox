"""
浏览器自动化工具：一个 Browser 对象，把"打开页面、点按钮、填表单"这些动作
包装得既简单又聪明，专门服务于逆向注册这类"点了不一定成功、要反复确认"的场景。

设计思想：
底层用 nodriver（直接驱动 Chrome，无需 WebDriver，反检测能力强）。但 nodriver 是
异步 API，且一个动作到底"成功没有"要自己判断。本文件解决两件事：

1. 同步化——内部维护一个事件循环，把所有异步调用藏起来，调用方写同步代码即可，
   不用到处 await，不用管事件循环。

2. 智能判断——这是核心。真实网页里，"点击"经常点了没反应（元素还没加载、被遮挡、
   动画未结束）。所以每个动作都支持"期望结果"参数：点完之后期望某元素出现 / 某文字
   出现 / 网址变化，动作会自己重试直到期望达成或超时。达成才算成功返回 True，
   否则返回 False。调用方一个 if 就知道这步成没成，不用自己写等待和轮询。

统一判断参数（点击/填写/打开等动作都支持）：
- appear   ：期望某个元素出现，才算成功
- vanish   ：期望某个元素消失，才算成功
- url_has  ：期望网址包含某段文字，才算成功
- text_has ：期望页面出现某段文字，才算成功
- tries    ：最多重试几次
- gap      ：每次重试之间隔几秒

选择器规则：
- 以 "css=" 开头   → 强制按 CSS 选择器查找，如 "css=button.submit"
- 以 "xpath=" 开头 → 强制按 XPath 查找，如 "xpath=//button[@type='submit']"
- 以 "text=" 开头  → 强制按页面可见文字查找，如 "text=注册"
- 无前缀时自动判断：以 / 或 ( 开头视为 XPath，含 CSS 特征字符视为 CSS，其余视为文字

里面有什么：
- Browser 类            浏览器实例，管生命周期和所有页面动作
- Browser.open()        打开网址
- Browser.click()       点击元素（支持智能判断）
- Browser.fill()        填写输入框（清空后整串写入）
- Browser.type()        逐字缓慢输入（触发 JS 逐字校验的站点必须用这个）
- Browser.fill_form()   一次填多个字段（传入字典 {选择器: 值}）
- Browser.upload()      上传文件
- Browser.wait()        等待元素/文字/网址满足条件
- Browser.exists()      判断元素是否存在
- Browser.value()       读取输入框当前值（回读校验用）
- Browser.text()        取元素或整页文字
- Browser.html()        取元素或整页 HTML 源码
- Browser.cookie()      取某个 cookie 值（逆向拿 token 常用）
- Browser.cookies()     取全部 cookie 为字典
- Browser.switch()      切换到新标签页（OAuth 弹窗场景）
- Browser.tabs()        列出所有标签页的网址
- Browser.shot()        截图
- Browser.run_js()      执行 JS 并取返回值

怎么调用：
    from tools.browser import Browser

    with Browser(headless=False) as page:
        page.open("https://example.com/sign_up")
        page.fill("#email", "a@b.com")
        # 点注册，并要求点完后网址离开注册页才算成功
        ok = page.click("#submit", url_has="/welcome")
        if ok:
            token = page.cookie("sso")             # 注册成功，取出登录 token
"""

import asyncio                                              # 标准异步库，用来自建并驱动事件循环
import json                                                 # 安全编码下拉框值，避免拼接 JS 时引号冲突
import sys                                                  # Windows 真人操作阶段需要恢复浏览器窗口
import time                                                 # 用于重试之间的等待和超时计时
import nodriver                                             # 底层浏览器引擎，直接和 Chrome 通信


# 一批让浏览器更"干净"的启动参数：关掉密码保存气泡、通知、后台节流等
# 逆向注册时这些弹窗会挡住按钮、拖慢流程，模块级常量集中管理便于统一调整
clean_args = [
    "--disable-save-password-bubble",                       # 关掉"是否保存密码"气泡，它会盖住注册按钮
    "--disable-notifications",                              # 关掉网站通知请求弹窗
    "--disable-infobars",                                   # 关掉顶部"Chrome 正被自动化控制"提示条
    "--no-default-browser-check",                           # 跳过默认浏览器检查弹窗
    "--disable-background-timer-throttling",                # 禁止后台标签页降速，保证脚本稳定执行
    "--disable-renderer-backgrounding",                     # 同上，防止窗口失焦后渲染被暂停
    "--disable-backgrounding-occluded-windows",             # Windows 窗口被遮挡时仍保持渲染和事件处理
    "--disable-features=CalculateNativeWinOcclusion",       # 禁止 Chrome 按原生窗口遮挡状态冻结页面
]


class Browser:
    """一个浏览器实例，同步接口 + 内置智能判断。"""

    # --- 初始化：记录配置并启动浏览器 ---
    def __init__(self, headless=False, proxy=None, window=(1280, 800), args=None, auto_start=True):
        # headless：是否无界面运行，调试时设 False 能看到浏览器
        # proxy：代理地址，需要换 IP 时传入
        # window：初始窗口尺寸，(宽, 高)
        # args：额外的 Chrome 启动参数，会和内置 clean_args 合并
        # auto_start：是否在创建时就启动浏览器，默认是
        self.headless = headless                            # 保存无头开关，start 时用
        self.proxy = proxy                                  # 保存代理设置
        self.window = window                                # 保存窗口尺寸
        self.extra_args = args or []                        # 保存额外启动参数，None 归一成空列表
        self.loop = None                                    # 事件循环，start 时创建，驱动所有异步调用
        self.driver = None                                  # nodriver 的浏览器对象
        self.tab = None                                     # 当前操作的标签页，所有动作都作用在它上面

        if auto_start:                                      # 默认创建即启动，让调用方少写一步
            self.start()

    # --- 把一个异步调用同步执行 ---
    def _run(self, coroutine):
        # coroutine：nodriver 的某个异步操作
        # 用自己持有的事件循环跑完它并拿到结果，这样对外全是同步接口
        return self.loop.run_until_complete(coroutine)

    # --- 启动浏览器 ---
    def start(self):
        if self.tab is not None:                            # 已经启动过就不重复启动，避免开出多个浏览器进程
            return self

        self.loop = asyncio.new_event_loop()                # 为本实例单独建一个事件循环，和其他实例隔离
        asyncio.set_event_loop(self.loop)                   # 设为当前线程的默认循环，nodriver 内部会取用

        browser_args = clean_args + self.extra_args         # 合并"干净启动参数"和调用方的额外参数
        if self.proxy:                                      # 若配置了代理，追加代理启动参数
            browser_args.append(f"--proxy-server={self.proxy}")

        self.driver = self._run(nodriver.start(             # 真正拉起 Chrome 进程
            headless=self.headless,                         # 是否无头
            browser_args=browser_args,                      # 传入合并后的启动参数
        ))
        self.tab = self.driver.main_tab                     # 取主标签页作为后续所有动作的操作对象
        self._run(self.tab.set_window_size(0, 0, *self.window))  # 设置窗口尺寸，前两个参数是左上角坐标
        return self                                         # 返回自身，支持链式写法

    # --- 关闭浏览器，释放进程和事件循环 ---
    def close(self):
        if self.driver:                                     # 有浏览器才需要关
            try:
                connections = [*self.driver.tabs, getattr(self.driver, "connection", None)]
                for connection in connections:             # 每个标签页和浏览器本身都有独立 WebSocket
                    if connection:
                        self._run(connection.disconnect())  # 逐个等到真正断开，不能只排队后立刻关事件循环
                process = getattr(self.driver, "_process", None)
                if process:
                    process.terminate()                     # nodriver.stop 会重复排队断连，这里只负责结束进程
                    self._run(process.wait())               # 等 returncode 更新，避免 atexit 再次误判为运行中
            except Exception:
                pass                                        # 关闭时的异常不影响主流程，静默忽略
        if self.loop:                                       # 有事件循环才需要关
            try:
                self._drain()                               # 先跑完关闭 Chrome 时挂起的收尾回调
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                self.loop.close()                           # 收尾完成后再关循环
            except Exception:
                pass
        self.driver = None                                  # 清空引用，标记已关闭
        self.tab = None
        self.loop = None

    # --- 给事件循环一点时间跑完挂起的收尾回调 ---
    def _drain(self):
        try:
            pending = [task for task in asyncio.all_tasks(self.loop) if not task.done()]
            for task in pending:
                task.cancel()                               # update_targets/keepalive 等后台任务不能留到关循环后
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(asyncio.sleep(0.25))
        except Exception:
            pass

    # ==================== 元素查找层 ====================

    # --- 按选择器找一个元素，找不到返回 None ---
    def locate(self, selector, timeout=8):
        # selector：CSS 选择器、XPath、页面可见文字，或带 css=/xpath=/text= 前缀的显式指定
        # timeout：最多找多少秒
        # 返回元素对象或 None，是所有动作定位元素的统一入口
        if isinstance(selector, (list, tuple)):            # 候选列表按顺序尝试，适合多语言文案和新版/旧版页面
            each_timeout = max(0.5, timeout / max(1, len(selector)))  # 总等待时间大致保持不变
            for item in selector:
                element = self.locate(item, timeout=each_timeout)  # 递归复用单选择器规则
                if element:
                    return element                         # 第一个命中的候选立即返回
            return None                                    # 所有候选都找不到
        try:
            kind, expr = self._parse_selector(selector)    # 拆分前缀和表达式，统一判定类型
            if kind == "xpath":
                elements = self._run(self.tab.xpath(expr, timeout=timeout))
                return elements[0] if elements else None
            if kind == "css":
                return self._run(self.tab.select(expr, timeout=timeout))
            if kind == "text":
                return self._run(self.tab.find(expr, timeout=timeout))  # 显式文字查找
            # 自动模式先按 CSS 查找；纯标签 button/input 也能正确定位。CSS 找不到再按可见文字兜底。
            try:
                element = self._run(self.tab.select(expr, timeout=min(timeout, 2)))
                if element:
                    return element
            except Exception:
                pass                                       # 普通文字可能不是合法 CSS，继续走文字查找
            return self._run(self.tab.find(expr, timeout=timeout))
        except Exception:
            return None                                     # 超时或选择器无效都归为"没找到"

    # --- 解析选择器，返回 (类型, 表达式) ---
    def _parse_selector(self, selector):
        # 支持显式前缀 css= / xpath= / text=，优先使用；无前缀时自动推断
        if selector.startswith("css="):                     # 显式 CSS
            return ("css", selector[4:])
        if selector.startswith("xpath="):                   # 显式 XPath
            return ("xpath", selector[6:])
        if selector.startswith("text="):                    # 显式文字
            return ("text", selector[5:])

        # 自动推断：以 / 或 ( 开头 → XPath
        if selector.startswith("/") or selector.startswith("("):
            return ("xpath", selector)
        # 含典型 CSS 特征字符 → CSS（包括 tag.class、tag#id、属性选择器等）
        if any(ch in selector for ch in ".#[:>~+"):
            return ("css", selector)
        # 其余进入自动模式：先试 CSS 标签名，再按页面可见文字找
        return ("auto", selector)

    # --- 判断元素是否存在 ---
    def exists(self, selector, timeout=3):
        return self.locate(selector, timeout=timeout) is not None

    # --- 判断元素当前是否真正可见 ---
    def visible(self, selector, timeout=3):
        # DOM 里存在不等于能看到；display:none、visibility:hidden、零尺寸都应返回 False
        try:
            element = self.locate(selector, timeout=timeout)  # 先定位真实元素
            if not element:
                return False
            script = "(elem) => { const s=getComputedStyle(elem); const r=elem.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)!==0 && r.width>0 && r.height>0; }"
            return bool(self._run(element.apply(script)))   # 在元素上下文读取实时样式和尺寸
        except Exception:
            return False

    # ==================== 智能判断核心 ====================

    # --- 检查一组"期望结果"是否全部达成 ---
    def check(self, appear=None, vanish=None, url_has=None, text_has=None):
        if appear and not self.visible(appear, timeout=1):  # appear 的自然含义是“用户看得见”，不只是 DOM 已挂载
            return False
        if vanish and self.visible(vanish, timeout=1):      # 隐藏但仍留在 DOM 里也算已经消失
            return False
        if url_has and url_has not in self.url():
            return False
        if text_has and text_has not in self.text():
            return False
        return True

    # --- 反复执行一个动作直到期望达成或超时（所有智能动作的引擎） ---
    def act(self, do, appear=None, vanish=None, url_has=None, text_has=None, tries=3, gap=1.0, replay=True):
        has_expect = any([appear, vanish, url_has, text_has])
        did_run = False                                    # 记录动作是否已经真正执行成功

        for attempt in range(tries):
            if not did_run or replay:                      # 可重放动作每轮执行；副作用动作成功执行一次后只检查结果
                done = do()
                did_run = did_run or done                  # 定位失败不算执行，后续仍可继续找元素
            else:
                done = True                                # 已点击过，本轮只轮询后置条件，不重复点击

            if not has_expect:
                if done:
                    return True
            else:
                if done and self.check(appear, vanish, url_has, text_has):
                    return True

            time.sleep(gap)

        return False

    # ==================== 页面动作层 ====================

    # --- 打开网址 ---
    def open(self, url, appear=None, text_has=None, tries=2, gap=1.0):
        # url：要打开的地址
        def do():
            self._run(self.tab.get(url))
            return True

        return self.act(do, appear=appear, text_has=text_has, tries=tries, gap=gap)

    # --- 点击元素 ---
    def click(
        self, selector, appear=None, vanish=None, url_has=None, text_has=None,
        tries=3, gap=1.0, repeat=False, skip_if_done=False,
    ):
        # selector：要点击的元素
        # repeat：默认 False，成功点击一次后只等待结果，避免重复注册/发码；明确需要连点时才开启
        # skip_if_done：恢复中断流程时可设 True，若目标状态已成立就不再点击
        has_expect = any([appear, vanish, url_has, text_has])  # 是否提供了可验证的成功结果
        if skip_if_done and has_expect and self.check(appear, vanish, url_has, text_has):
            return True                                    # 目标状态本来就成立，直接成功，避免恢复运行时重复提交

        def do():
            element = self.locate(selector)
            if not element:
                return False
            self._run(self.tab.activate())                  # 后台窗口先激活当前 target，保证 CDP 鼠标事件被页面接收
            self._run(element.scroll_into_view())
            self._run(element.focus())                      # 元素获得页面焦点后再点击，避免失焦窗口吞掉事件
            self._run(element.click())
            return True

        return self.act(
            do, appear=appear, vanish=vanish, url_has=url_has, text_has=text_has,
            tries=tries, gap=gap, replay=repeat,
        )

    # --- 填写输入框（清空后整串写入，速度快） ---
    def fill(self, selector, value, appear=None, vanish=None, verify=False, value_is=None, tries=3, gap=1.0):
        # selector：目标输入框
        # value：要填入的文字
        # verify：填完后是否回读校验
        # value_is：指定期望读回值；传入后自动开启校验，不传则期望值就是 value
        expected = str(value if value_is is None else value_is)  # 统一期望类型，避免数字和字符串比较失败
        def do():
            element = self.locate(selector)
            if not element:
                return False
            self._run(element.scroll_into_view())
            self._run(element.clear_input())
            self._run(element.send_keys(str(value)))
            if verify or value_is is not None:              # 需要回读校验：填完立刻读回来精确对比
                actual = self._get_value(element)
                if actual != expected:                      # 表单实际值和期望不同 → 填写失败并触发重试
                    return False
            return True

        return self.act(do, appear=appear, vanish=vanish, tries=tries, gap=gap)

    # --- 逐字缓慢输入（触发 JS 逐字事件校验的站点必须用这个） ---
    def type(
        self, selector, value, delay=0.05, appear=None, vanish=None,
        verify=False, value_is=None, tries=3, gap=1.0, repeat=False,
    ):
        # delay：每个字符之间的间隔秒数，模拟真人打字速度
        # repeat：默认只输入一次并等待后置条件，避免 OTP 自动提交后再次输入；明确需要重填才开启
        expected = str(value if value_is is None else value_is)  # 输入完成后用于精确回读

        def do():
            element = self.locate(selector)
            if not element:
                return False
            self._run(element.scroll_into_view())
            self._run(element.clear_input())
            for char in str(value):                         # 逐字符一个一个输入
                self._run(element.send_keys(char))
                time.sleep(delay)                           # 每个字符之间暂停一下，让 JS 事件来得及触发
            if verify or value_is is not None:
                actual = self._get_value(element)
                if actual != expected:
                    return False
            return True

        return self.act(do, appear=appear, vanish=vanish, tries=tries, gap=gap, replay=repeat)

    # --- 一次填多个字段（传入字典 {选择器: 值}） ---
    def fill_form(self, fields, verify=False, tries=3, gap=0.5):
        # fields：字典，键是输入框选择器，值是要填入的文字
        # 返回 True 表示全部字段都填成功
        for selector, value in fields.items():
            ok = self.fill(selector, value, verify=verify, tries=tries, gap=gap)
            if not ok:
                return False                                # 任何一个字段填失败就整体失败
        return True

    # --- 上传文件 ---
    def upload(self, selector, filepath):
        # selector：文件上传的 input[type=file] 元素
        # filepath：要上传的本地文件路径
        try:
            element = self.locate(selector)
            if not element:
                return False
            self._run(element.send_file(filepath))          # nodriver 的文件上传方法
            return True
        except Exception:
            return False

    # --- 在元素上按键 ---
    def press(self, selector, key):
        # 常用控制键转成 nodriver 能发送的字符；普通文字会原样输入
        keys = {"Enter": "\n", "Tab": "\t", "Escape": "\x1b", "Backspace": "\b"}
        try:
            element = self.locate(selector)                # 按键前先把目标输入框或按钮找出来
            if not element:
                return False
            self._run(element.focus())                     # 聚焦后按键才会发给正确元素
            self._run(element.send_keys(keys.get(key, key)))  # 控制键转换，普通字符原样发送
            return True
        except Exception:
            return False

    # --- 鼠标移到元素上 ---
    def hover(self, selector):
        try:
            element = self.locate(selector)                # 找到要悬停的菜单或按钮
            if not element:
                return False
            self._run(element.scroll_into_view())          # 先滚进视口再移动鼠标
            self._run(element.mouse_move())                # 移到元素中心，触发 mouseover/hover
            return True
        except Exception:
            return False

    # --- 设置复选框状态 ---
    def check_box(self, selector, checked=True):
        try:
            element = self.locate(selector)                # 找到 checkbox/radio 元素
            if not element:
                return False
            wanted = "true" if checked else "false"        # 转成 JS 布尔字面量
            script = f"(elem) => {{ if (Boolean(elem.checked) !== {wanted}) elem.click(); return Boolean(elem.checked); }}"
            actual = self._run(element.apply(script))      # 状态不同时点击一次，并返回最终状态
            return bool(actual) is bool(checked)           # 最终状态符合期望才算成功
        except Exception:
            return False

    # --- 选择下拉框选项 ---
    def select(self, selector, value):
        try:
            element = self.locate(selector)                # 找到 select 元素
            if not element:
                return False
            safe = json.dumps(str(value))                  # JSON 编码避免值里的引号破坏 JS
            actual = self._run(element.apply(f"(elem) => {{ elem.value = {safe}; return elem.value; }}"))
            if str(actual) != str(value):                   # 浏览器没有接受该 value，通常是 option 不存在
                return False
            try:
                # 某些页面的隔离执行环境不提供 Event 构造器，所以事件派发单独兜底，不影响已设置的值。
                script = "(elem) => { elem.dispatchEvent(new Event('input', {bubbles:true})); elem.dispatchEvent(new Event('change', {bubbles:true})); }"
                self._run(element.apply(script))           # 通知 React/Vue 等前端框架下拉值已变化
            except Exception:
                pass                                       # 值已经回读成功，事件环境差异不应把操作误判为失败
            return True                                    # 设置和回读都成功
        except Exception:
            return False

    # --- 从页面移除元素（遮挡层/广告等调试场景） ---
    def remove(self, selector):
        try:
            element = self.locate(selector)                # 找到要移除的遮挡元素
            if not element:
                return True                                # 本来就不存在，目标状态已经达成
            self._run(element.remove_from_dom())           # 直接从 DOM 删除
            return not self.exists(selector, timeout=1)    # 确认确实消失
        except Exception:
            return False

    # --- 刷新当前页面 ---
    def reload(self, appear=None, text_has=None, timeout=15):
        try:
            self._run(self.tab.reload())                   # 让当前标签页重新加载
            return self.wait(appear=appear, text_has=text_has, timeout=timeout) if (appear or text_has) else True
        except Exception:
            return False

    # --- 浏览历史后退 ---
    def back(self):
        try:
            self._run(self.tab.back())                     # 回到上一条浏览历史
            return True
        except Exception:
            return False

    # --- 浏览历史前进 ---
    def forward(self):
        try:
            self._run(self.tab.forward())                  # 前往下一条浏览历史
            return True
        except Exception:
            return False

    # --- 明确等待固定秒数 ---
    def sleep(self, seconds):
        time.sleep(max(0, seconds))                        # 负数归零，避免 time.sleep 抛错
        return self                                        # 返回自身便于 page.sleep(1).click(...) 链式调用

    # --- 等待某个条件达成 ---
    def wait(self, appear=None, vanish=None, url_has=None, text_has=None, timeout=15, gap=0.5):
        # 只等待、不执行动作，用于"提交后等结果页出现"这类纯等待场景
        deadline = time.time() + timeout

        while time.time() < deadline:
            if self.check(appear, vanish, url_has, text_has):
                return True
            time.sleep(gap)

        return False

    # ==================== 标签页管理 ====================

    # --- 把有头 Chrome 恢复到前台（只在需要真人操作时调用） ---
    def front(self):
        if self.headless or sys.platform != "win32" or not self.driver:
            return False
        try:
            import ctypes                                   # 标准库直接调用 Windows 窗口 API，不新增依赖
            import winsound                                 # 后台流程抵达真人步骤时给用户明确提示
            from ctypes import wintypes

            process = getattr(self.driver, "_process", None)
            pid = process.pid if process else 0
            windows = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def find_window(hwnd, _):
                owner = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
                    windows.append(hwnd)
                    return False                            # 找到主 Chrome 窗口即可停止枚举
                return True

            ctypes.windll.user32.EnumWindows(find_window, 0)
            if not windows:
                return False
            hwnd = windows[0]
            ctypes.windll.user32.ShowWindow(hwnd, 9)        # SW_RESTORE：从最小化状态恢复
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            winsound.MessageBeep()                          # 即使系统拒绝抢焦点，仍能提醒用户切换窗口
            return True
        except Exception:
            return False                                    # 前台提醒失败不能影响自动注册主流程

    # --- 切换到另一个标签页（OAuth 弹窗、新窗口等场景） ---
    def switch(self, index=-1, url_has=None):
        # index：切到第几个标签页，-1 表示最新一个（通常是刚弹出来的）
        # url_has：只切到网址包含该文字的标签页
        try:
            self._run(self.driver.update_targets())         # 刷新标签页列表，确保刚弹出的也能看到
            all_tabs = self.driver.tabs                     # 所有打开的标签页

            if url_has:                                     # 按网址关键词找目标标签页
                for tab in all_tabs:
                    tab_url = tab.target.url if hasattr(tab, "target") else ""
                    if url_has in tab_url:
                        self.tab = tab
                        self._run(self.tab.activate())      # 激活目标标签页
                        return True
                return False                                # 没找到含该关键词的标签页

            if abs(index) <= len(all_tabs):                 # 按序号切换
                self.tab = all_tabs[index]
                self._run(self.tab.activate())
                return True
            return False
        except Exception:
            return False

    # --- 列出所有标签页的网址 ---
    def tabs(self):
        try:
            self._run(self.driver.update_targets())
            return [t.target.url for t in self.driver.tabs if hasattr(t, "target")]
        except Exception:
            return []

    # ==================== 读取层 ====================

    # --- 取当前网址 ---
    def url(self):
        try:
            return self._run(self.tab.evaluate("document.URL")) or ""
        except Exception:
            return ""

    # --- 取元素文字，不传选择器则取整页文字 ---
    def text(self, selector=None):
        try:
            if selector is None:
                return self._run(self.tab.evaluate("document.body.innerText")) or ""
            element = self.locate(selector)
            return element.text if element else ""
        except Exception:
            return ""

    # --- 取元素或整页 HTML 源码 ---
    def html(self, selector=None):
        try:
            if selector is None:
                return self._run(self.tab.evaluate("document.documentElement.outerHTML")) or ""
            element = self.locate(selector)
            if not element:
                return ""
            return self._run(element.get_html()) or ""
        except Exception:
            return ""

    # --- 读取输入框当前值（回读校验时的核心） ---
    def value(self, selector):
        # selector：目标输入框
        try:
            element = self.locate(selector)
            return self._get_value(element) if element else ""
        except Exception:
            return ""

    # --- 内部：从元素对象上取 value 属性 ---
    def _get_value(self, element):
        try:
            # apply 会让 nodriver 用 backend_node_id 定位真实 DOM 元素，再读取实时 value。
            # 这比读取 attrs 可靠，因为 attrs 往往还是页面初始值，不会随用户输入更新。
            result = self._run(element.apply("(elem) => elem.value"))
            return "" if result is None else str(result)
        except Exception:
            return ""

    # --- 取某个 cookie 的值 ---
    def cookie(self, name):
        try:
            cookies = self._run(self.driver.cookies.get_all())
            for item in cookies:
                if item.name == name:
                    return item.value
            return ""
        except Exception:
            return ""

    # --- 取全部 cookie 为字典 ---
    def cookies(self):
        try:
            items = self._run(self.driver.cookies.get_all())
            return {item.name: item.value for item in items}
        except Exception:
            return {}

    # --- 截图保存到文件 ---
    def shot(self, path="shot.png"):
        try:
            self._run(self.tab.save_screenshot(path))
            return path
        except Exception:
            return ""

    # --- 执行一段 JS 并返回结果 ---
    def run_js(self, script):
        try:
            return self._run(self.tab.evaluate(script))
        except Exception:
            return None

    # --- 支持 with 语法：进入返回自身 ---
    def __enter__(self):
        return self

    # --- 支持 with 语法：退出时自动关闭浏览器 ---
    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
