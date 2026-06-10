"""
worldrouter.ai 自动注册工具

这个文件自动完成 worldrouter.ai 账号注册全流程：生成临时邮箱 → 填写注册表单 → 获取验证码 → 完成注册 → 提取 API Key

主要功能：
- register() 执行完整注册流程
- extractCode() 从邮件中提取验证码
- waitForCredit() 等待 credit 额度从 0 增加到 100
- createApiKey() 创建并提取 API Key
- runBatch() 批量注册指定数量账号

调用示例：
asyncio.run(runBatch(5))  # 批量注册 5 个账号
asyncio.run(register())    # 注册单个账号
"""

import sys
from pathlib import Path

# 获取当前文件的绝对路径
current_file_path = Path(__file__).resolve()
# 获取项目根目录（LazyFox 文件夹）
# worldrouter.py 在 register 文件夹下，所以 .parent 取到 register，再 .parent 取到 LazyFox
project_root = current_file_path.parent.parent

# 将根目录加入 sys.path 的第一位，确保优先搜索这里
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


import nodriver as uc
import asyncio
import inspect
import re
from bs4 import BeautifulSoup
from TempMail.gptmail import TempMail
from tools.identity import Identity
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.table import Table

console = Console()  # 创建控制台对象用于美化输出
idg = Identity()  # 创建身份生成器用于生成随机姓名和密码


async def closeBrowser(browser):
    """安全关闭浏览器，兼容 nodriver 的同步 stop 和异步 stop，避免 None 被 await 报错。"""

    if not browser:
        return  # 没有浏览器对象时无需关闭

    try:
        stop_result = browser.stop()  # 当前 nodriver 版本里 stop 多数是同步方法，会直接返回 None
        if inspect.isawaitable(stop_result):
            await stop_result  # 兼容少数版本返回协程的情况
        await asyncio.sleep(0.5)  # 给 websocket 和临时 profile 半秒时间做后台清理
    except Exception as error:
        console.print(f"[yellow]浏览器关闭时出现可忽略异常: {error}[/yellow]")


async def register():
    """执行完整注册流程，成功后保存 API Key 到文件，返回是否成功"""

    # 生成临时邮箱（带重试机制处理 SSL 错误）
    mail = None
    email = None
    max_retries = 3  # 最多重试3次

    for attempt in range(max_retries):
        try:
            mail = TempMail()  # 创建临时邮箱实例
            email = mail.generateEmail()  # 生成临时邮箱地址
            console.print(f"[cyan]临时邮箱:[/cyan] {email}")
            break  # 成功则跳出循环
        except Exception as e:
            error_msg = str(e)
            if "SSL" in error_msg or "EOF" in error_msg:
                console.print(f"[yellow]临时邮箱服务连接失败 (尝试 {attempt + 1}/{max_retries}): SSL错误[/yellow]")
                if mail:
                    try:
                        mail.close()  # 清理失败的连接
                    except:
                        pass
                mail = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)  # 等待2秒后重试
                    continue
            console.print(f"[red]临时邮箱生成失败: {e}[/red]")
            return False

    if not mail or not email:
        console.print("[red]无法生成临时邮箱，请检查网络连接或更换节点[/red]")
        return False

    try:
        # 将后续代码包装在 try 块中，确保出错时能关闭邮箱连接

        browser = await uc.start(headless=False)  # 启动浏览器（非无头模式方便调试）
        tab = await browser.get("https://router.worldclaw.ai/login?returnTo=%2Fdashboard")  # 打开登录页面
        await tab.sleep(5)  # 等待页面完全加载（增加等待时间确保页面就绪）

        # 点击注册按钮
        try:
            signup_link = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > p > a", timeout=10)
            await signup_link.click()  # 点击"注册"链接跳转到注册页面
            await tab.sleep(4)  # 等待注册页面完全加载
        except Exception as e:
            console.print(f"[red]找不到注册按钮: {e}[/red]")
            await closeBrowser(browser)
            return False

        # 填写姓名和邮箱
        full_name_en = idg.fullName(locale="en")  # 生成英文全名
        name_parts = full_name_en.split()  # 拆分成名和姓
        first_name = name_parts[0]  # 提取名
        last_name = name_parts[-1]  # 提取姓
        password = idg.password()  # 生成随机密码

        console.print(f"[cyan]姓名:[/cyan] {first_name} {last_name}")

        # 输入名（使用 find 方法更稳健）
        try:
            first_name_input = await tab.select("#radix-\\:r0\\:", timeout=5)
            if first_name_input:
                await first_name_input.send_keys(first_name)  # 输入名
            else:
                raise Exception("找不到名字输入框")
        except Exception as e:
            console.print(f"[red]名字输入失败: {e}[/red]")
            await closeBrowser(browser)
            return False

        # 输入姓
        try:
            last_name_input = await tab.select("#radix-\\:r1\\:", timeout=5)
            if last_name_input:
                await last_name_input.send_keys(last_name)  # 输入姓
            else:
                raise Exception("找不到姓氏输入框")
        except Exception as e:
            console.print(f"[red]姓氏输入失败: {e}[/red]")
            await closeBrowser(browser)
            return False

        # 输入邮箱
        try:
            email_input = await tab.select("#radix-\\:r2\\:", timeout=5)
            if email_input:
                await email_input.send_keys(email)  # 输入邮箱
            else:
                raise Exception("找不到邮箱输入框")
        except Exception as e:
            console.print(f"[red]邮箱输入失败: {e}[/red]")
            await closeBrowser(browser)
            return False

        await tab.sleep(1)  # 等待输入完成

        # 点击继续按钮
        continue_btn = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > form > div > div.rt-Flex.rt-r-fd-column.rt-r-gap-5.ak-AuthForm > button", timeout=5)
        await continue_btn.click()  # 点击继续按钮
        await tab.sleep(3)  # 等待服务器验证邮箱

        # 检查邮箱是否被阻止
        try:
            error = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > form > div > div.rt-Text.rt-r-size-2.rt-r-ta-center.ak-ErrorMessage", timeout=3)
            if error:
                error_text = await error.text  # 读取错误提示文本
                if "访问被阻止" in error_text or "blocked" in error_text.lower():
                    console.print("[red]邮箱域名被阻止，请联系支持[/red]")
                    await closeBrowser(browser)
                    return False
        except:
            pass  # 没有错误提示说明邮箱可用，继续流程

        # 输入密码（等待页面切换到密码输入步骤）
        console.print("[cyan]等待密码输入页面...[/cyan]")
        await tab.sleep(2)  # 给页面更多时间完成切换

        try:
            # 尝试多种方式找到密码输入框
            password_input = None

            # 方式1: 使用原始 CSS 选择器（可能 ID 会变化）
            try:
                password_input = await tab.select("#radix-\\:ra\\:", timeout=3)
            except:
                pass

            # 方式2: 使用 input[type="password"] 通用选择器
            if not password_input:
                try:
                    password_input = await tab.find("input[type='password']", timeout=3)
                except:
                    pass

            # 方式3: 使用 XPath 查找密码输入框
            if not password_input:
                try:
                    # 根据原注释的 XPath，密码框可能在 form 中
                    inputs = await tab.find_all("input")
                    for inp in inputs:
                        input_type = await inp.get_attribute("type")
                        if input_type == "password":
                            password_input = inp
                            break
                except:
                    pass

            if password_input:
                await password_input.send_keys(password)  # 输入密码
                console.print(f"[cyan]密码:[/cyan] {password}")
            else:
                raise Exception("找不到密码输入框，页面可能未正确加载")

        except Exception as e:
            console.print(f"[red]密码输入失败: {e}[/red]")
            console.print("[yellow]提示: 页面可能还在加载，或者选择器已变化[/yellow]")
            await closeBrowser(browser)
            return False

        await tab.sleep(2)  # 等待输入完成和页面响应

        continue_btn2 = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > form > div > div.rt-Flex.rt-r-fd-column.rt-r-gap-5.ak-AuthForm > button", timeout=5)
        await continue_btn2.click()  # 点击继续按钮
        await tab.sleep(3)  # 等待服务器响应

        # 检查是否出现"访问被阻止"错误（在输入密码后）
        console.print("[cyan]检查是否被阻止...[/cyan]")
        error_element = None  # 先把结果放在 try 外面，避免关闭浏览器的异常被误判成"没找到元素"
        try:
            # 使用精确的选择器查找错误提示元素（只要找到就说明被阻止了）
            error_element = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > form > div > div.rt-Text.rt-r-size-2.rt-r-ta-center.ak-ErrorMessage", timeout=3)
        except Exception:
            # tab.find 超时或找不到时会抛异常，这只代表当前页面没有错误提示元素。
            error_element = None

        if error_element:
            # 找到错误元素就直接终止本次注册，不再读取验证码，不再进入后续流程。
            error_text = error_element.text  # text 是属性不是方法，不需要 await
            console.print(f"[red]✗ 检测到错误提示: {error_text}[/red]")
            console.print("[red]访问被阻止，终止本次注册[/red]")
            await closeBrowser(browser)
            return False

        console.print("[green]✓ 未检测到错误元素，继续流程[/green]")
        await tab.sleep(2)  # 等待验证码输入页面完全加载

        # 获取验证码
        code = await extractCode(mail)  # 从临时邮箱中提取验证码
        if not code:
            console.print("[red]未获取到验证码[/red]")
            await closeBrowser(browser)
            return False

        # 输入验证码（只需输入第一个输入框，其他会自动填充）
        code_input = await tab.find("body > div:nth-child(1) > div > div > div > div.rt-reset.rt-BaseCard.rt-Card.rt-r-size-4.xs\\:rt-r-size-5.rt-variant-surface.ak-Card > form:nth-child(2) > div > div > div > div:nth-child(1) > input", timeout=5)
        await code_input.send_keys(code)  # 输入验证码
        await tab.sleep(8)  # 等待验证码验证并跳转到 dashboard（增加等待时间）

        # 等待 credit 额度增加
        console.print("[cyan]等待 credit 额度增加到 100...[/cyan]")
        credit_ready = await waitForCredit(tab)  # 轮询检查 credit 是否就绪
        if not credit_ready:
            console.print("[red]credit 额度未增加，注册可能失败[/red]")
            await closeBrowser(browser)
            return False

        # 创建 API Key
        api_key = await createApiKey(tab)  # 创建并提取 API Key
        if not api_key:
            console.print("[red]未能创建或提取 API Key[/red]")
            await closeBrowser(browser)
            return False

        # 保存结果
        saveApiKey(api_key, email, password)  # 保存 API Key 和账号信息到文件
        console.print(f"[green]✓ 注册成功[/green] API Key: {api_key[:20]}...")
        await closeBrowser(browser)
        return True

    finally:
        # 确保无论成功失败都关闭邮箱连接
        if mail:
            try:
                mail.close()
            except:
                pass


async def extractCode(mail):
    """从临时邮箱中提取验证码，返回验证码字符串"""

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as progress:
        task = progress.add_task("等待验证码...", total=None)  # 创建进度任务

        for attempt in range(15):  # 最多尝试 15 次
            progress.update(task, description=f"检查邮箱 ({attempt + 1}/15)")

            inbox = mail.listAll()  # 获取所有邮件
            if not inbox:
                await asyncio.sleep(7)  # 没有邮件时等待 7 秒后重试
                continue

            latest = inbox[0]  # 取最新一封邮件
            mail_id = latest.get("mailID") or latest.get("messageID")  # 兼容不同字段名
            if not mail_id:
                await asyncio.sleep(7)
                continue

            body = mail.readMessage(mail_id)  # 读取邮件正文
            full_text = f"{latest.get('subject', '')}\n{body}\n{latest.get('textBody', '')}\n{latest.get('htmlBody', '')}"  # 拼接所有可能包含验证码的文本

            # 方法1：使用 TempMail 自带的 findCode
            code = mail.findCode(full_text)  # 尝试用通用正则提取验证码
            if code and len(str(code)) >= 4:  # 验证码长度至少 4 位
                console.print(f"[green]✓ 验证码:[/green] {code}")
                return code

            # 方法2：从 HTML 中提取加粗大字验证码
            soup = BeautifulSoup(body or latest.get("htmlBody", ""), "html.parser")
            for tag in soup.find_all(["strong", "b", "span", "td"]):  # 遍历可能包含验证码的标签
                text = tag.get_text(strip=True)  # 提取标签内文本
                if re.match(r"^\d{4,8}$", text):  # 匹配 4 到 8 位纯数字
                    console.print(f"[green]✓ 验证码:[/green] {text}")
                    return text

            # 方法3：正则匹配各种验证码格式
            patterns = [
                r"\b\d{6}\b",  # 6 位数字
                r"\b\d{5}\b",  # 5 位数字
                r"\b\d{4}\b",  # 4 位数字
                r"code[:\s]+(\d{4,8})",  # code: 123456 格式
                r"verification[:\s]+(\d{4,8})",  # verification: 123456 格式
            ]
            for pattern in patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)  # 忽略大小写搜索
                if match:
                    code = match.group(1) if match.lastindex else match.group(0)  # 提取捕获组或完整匹配
                    console.print(f"[green]✓ 验证码:[/green] {code}")
                    return code

            await asyncio.sleep(7)  # 未找到验证码时等待后重试

    return None  # 超时仍未获取到验证码时返回 None


async def waitForCredit(tab, timeoutSeconds=120):
    """等待 credit 额度从 0 增加到 100，返回是否成功"""

    start_time = asyncio.get_event_loop().time()  # 记录开始时间

    while asyncio.get_event_loop().time() - start_time < timeoutSeconds:  # 在超时时间内循环检查
        try:
            credit_element = await tab.find("body > main > div > main > div > div > section.space-y-5 > div.space-y-4 > div.hero-stat-grid.grid.gap-4.lg\\:grid-cols-2 > div:nth-child(1) > p.hero-stat-card-value", timeout=3)

            if not credit_element:  # 元素未找到
                console.print("[yellow]未找到 credit 元素，继续等待...[/yellow]")
                await asyncio.sleep(3)
                continue

            credit_text = credit_element.text  # 读取 credit 额度文本（text 是属性不需要 await）
            credit_value = int(re.search(r"\d+", credit_text).group())  # 提取数字部分

            console.print(f"[cyan]当前 credit: {credit_value}[/cyan]")

            if credit_value >= 100:  # credit 达到 100 表示账号已激活
                console.print("[green]✓ credit 额度已就绪[/green]")
                return True

            await asyncio.sleep(3)  # 未达到 100 时等待 3 秒后重新检查
        except Exception as e:
            console.print(f"[yellow]检查 credit 时出错: {e}[/yellow]")
            await asyncio.sleep(3)

    return False  # 超时仍未达到 100 时返回失败


async def createApiKey(tab):
    """创建 API Key 并提取返回，返回 API Key 字符串"""

    try:
        # 点击 Create API Keys 按钮
        create_button = await tab.find("body > main > div > main > div > div > section.ds-trading-banner > div.ds-trading-banner-copy > div > div > a", timeout=10)
        await create_button.click()  # 点击创建按钮
        await tab.sleep(3)  # 等待对话框完全弹出并加载

        # 输入 key 名称
        key_name = f"key_{idg.digits(6)}"  # 生成随机 key 名称
        name_input = await tab.find(
            "body > div.pointer-events-none.fixed.inset-0.z-\\[60\\].flex.items-center.justify-center.px-4.py-4.sm\\:px-6.sm\\:py-6 > div.pointer-events-auto.surface-card.ds-dialog-shell.ds-dialog-shell-scrollable.scrollbar-soft.max-w-\\[42rem\\].ds-create-key-shell > form > label > div > span > input", timeout=5
        )
        await name_input.send_keys(key_name)  # 输入 key 名称
        await tab.sleep(1.5)  # 等待输入完成

        # 点击 next 按钮
        next_button = await tab.find(
            "body > div.pointer-events-none.fixed.inset-0.z-\\[60\\].flex.items-center.justify-center.px-4.py-4.sm\\:px-6.sm\\:py-6 > div.pointer-events-auto.surface-card.ds-dialog-shell.ds-dialog-shell-scrollable.scrollbar-soft.max-w-\\[42rem\\].ds-create-key-shell > form > div.flex.justify-end > button", timeout=5
        )
        await next_button.click()  # 点击下一步按钮
        await tab.sleep(3)  # 等待 API Key 生成完成

        # 复制 API Key
        key_element = await tab.find(
            "body > div.pointer-events-none.fixed.inset-0.z-\\[60\\].flex.items-center.justify-center.px-4.py-4.sm\\:px-6.sm\\:py-6 > div.pointer-events-auto.surface-card.ds-dialog-shell.ds-dialog-shell-scrollable.scrollbar-soft.max-w-\\[42rem\\].ds-save-secret-shell > div.ds-save-secret-flow > div.ds-save-secret-card > div.ds-save-secret-code-row > code",
            timeout=8,
        )
        api_key = key_element.text  # 读取 API Key 文本，nodriver 的 text 是属性，不需要 await

        console.print(f"[green]✓ API Key 已创建:[/green] {api_key[:20]}...")
        return api_key.strip()  # 返回去除首尾空白的 API Key

    except Exception as e:
        console.print(f"[red]创建 API Key 时出错: {e}[/red]")
        return None


def saveApiKey(api_key, email, password):
    """保存 API Key 和账号信息到固定文件，避免运行目录变化导致找不到结果。"""

    save_path = project_root / "token" / "worldrouter_keys.txt"  # 固定保存到项目 token 目录
    save_path.parent.mkdir(parents=True, exist_ok=True)  # token 文件夹不存在时自动创建

    with save_path.open("a", encoding="utf-8") as file:  # 追加模式打开文件
        file.write(f"{api_key} | {email} | {password}\n")  # 按格式写入一行

    console.print(f"[green]✓ 结果已保存到:[/green] {save_path}")  # 明确告诉用户保存位置


async def runBatch(count):
    """批量注册指定数量的账号"""

    console.print(Panel.fit(f"[bold cyan]开始批量注册 {count} 个账号[/bold cyan]", border_style="cyan"))

    success = 0  # 成功计数
    failed = 0  # 失败计数

    for i in range(count):
        console.print(f"\n[bold yellow]═══ 第 {i + 1}/{count} 个账号 ═══[/bold yellow]")

        try:
            result = await register()  # 执行单次注册
            if result:
                success += 1  # 成功则累加成功计数
            else:
                failed += 1  # 失败则累加失败计数
        except Exception as e:
            console.print(f"[red]注册失败: {e}[/red]")
            failed += 1

        if i < count - 1:  # 不是最后一个账号时等待后继续
            console.print("[dim]等待 3 秒后继续...[/dim]")
            await asyncio.sleep(3)

    # 显示统计结果
    table = Table(title="注册统计", border_style="cyan")
    table.add_column("状态", style="cyan")
    table.add_column("数量", justify="right", style="green")
    table.add_row("成功", str(success))
    table.add_row("失败", str(failed))
    table.add_row("总计", str(count))

    console.print("\n")
    console.print(table)


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # Windows 平台设置事件循环策略

        # 获取用户输入的注册数量
        console.print("[bold cyan]worldrouter.ai 批量注册工具[/bold cyan]")
        count = console.input("[cyan]请输入注册数量:[/cyan] ")

        try:
            count = int(count)  # 转换为整数
            if count <= 0:
                console.print("[red]数量必须大于 0[/red]")
                sys.exit(1)
        except ValueError:
            console.print("[red]请输入有效数字[/red]")
            sys.exit(1)

        asyncio.run(runBatch(count))  # 运行批量注册

    except KeyboardInterrupt:
        console.print("\n[yellow]程序被用户中断[/yellow]")
