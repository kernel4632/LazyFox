"""
x.ai 自动注册工具
"""

import sys
from pathlib import Path

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # 抑制 pipe 关闭后的 ValueError 输出
    sys.stderr = open("NUL", "w") if False else sys.stderr  # 不推荐全局静默
    
    
current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import nodriver as uc
import asyncio
import re
from bs4 import BeautifulSoup
from TempMail.m2u import TempMail
from tools.identity import Identity
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.table import Table

console = Console()
idg = Identity()

# ═══════════════════════════════════════════════════════════════
# 智能等待辅助函数
# ═══════════════════════════════════════════════════════════════

DEFAULT_TIMEOUT = 15  # 默认超时秒数


async def wait_and_click(tab, selector, timeout=DEFAULT_TIMEOUT, description=""):
    """等待元素出现，然后点击它。失败时抛出异常"""
    desc = description or selector[:40]
    console.print(f"[dim]等待: {desc}[/dim]")
    element = await tab.find(selector, timeout=timeout)
    if not element:
        raise TimeoutError(f"超时未找到元素: {desc}")
    await element.click()
    console.print(f"[dim]✓ 已点击: {desc}[/dim]")
    return element


async def wait_and_type(tab, selector, text, timeout=DEFAULT_TIMEOUT, description=""):
    """等待输入框出现，然后输入文本"""
    desc = description or selector[:40]
    console.print(f"[dim]等待输入框: {desc}[/dim]")
    element = await tab.find(selector, timeout=timeout)
    if not element:
        raise TimeoutError(f"超时未找到输入框: {desc}")
    await element.send_keys(text)
    console.print(f"[dim]✓ 已输入: {desc}[/dim]")
    return element


async def wait_for_url_contains(tab, keyword, timeout=DEFAULT_TIMEOUT):
    """等待直到 URL 包含指定关键词"""
    console.print(f"[dim]等待URL包含: {keyword}[/dim]")
    for _ in range(timeout * 2):
        if keyword in tab.url:
            console.print(f"[dim]✓ URL已变为: {tab.url}[/dim]")
            return True
        await asyncio.sleep(0.5)
    raise TimeoutError(f"超时等待URL包含: {keyword}，当前: {tab.url}")


async def wait_for_url_not_contains(tab, keyword, timeout=DEFAULT_TIMEOUT):
    """等待直到 URL 不再包含指定关键词（说明页面跳转了）"""
    console.print(f"[dim]等待离开: {keyword}[/dim]")
    for _ in range(timeout * 2):
        if keyword not in tab.url:
            console.print(f"[dim]✓ 已离开，当前URL: {tab.url}[/dim]")
            return True
        await asyncio.sleep(0.5)
    return False  # 不抛异常，某些场景下跳转可能不发生


async def wait_for_text_on_page(tab, text, timeout=DEFAULT_TIMEOUT):
    """等待页面上出现指定文本"""
    for _ in range(timeout * 2):
        try:
            page_text = await tab.get_text()
            if text in page_text:
                return True
        except:
            pass
        await asyncio.sleep(0.5)
    return False


async def wait_for_element_gone(tab, selector, timeout=10):
    """等待某个元素消失（比如 loading 状态结束）"""
    for _ in range(timeout * 2):
        try:
            await tab.find(selector, timeout=0.3)
            await asyncio.sleep(0.5)
        except:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# 核心注册逻辑
# ═══════════════════════════════════════════════════════════════

# XPath / 选择器常量（集中管理，方便维护）
SELECTORS = {
    "email_option_btn": "/html/body/div[2]/div/div[1]/div[2]/div/div[2]/button[1]",
    "email_input": "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/input",
    "email_submit_btn": "/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]",
    "code_input": "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/div[1]/div[4]/input",
    "code_submit_btn": "/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]",
    "first_name_input": "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[1]/div/input",
    "last_name_input": "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[2]/div/input",
    "password_input": "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[2]/div/input",
    "signup_btn": "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[3]/button[1]",
}


async def register():
    """执行完整注册流程，成功后保存 token 到文件，返回是否成功"""

    with TempMail() as mail:
        # 生成邮箱（排除黑名单后缀）
        while True:
            email = mail.generateEmail()
            console.print(f"[cyan]临时邮箱:[/cyan] {email}")
            if email.endswith('kkb.qzz.io'):
                console.print("[yellow]检测到黑名单邮箱后缀，重新生成...[/yellow]")
                continue
            break

        browser = await uc.start(
            headless=False,
            args=[
                "--disable-save-password-bubble",
                "--no-default-browser-check",
                "--disable-infobars",
                "--disable-notifications",
            ],
        )

        try:
            tab = await browser.get("https://accounts.x.ai/sign-up")

            # ── 步骤1：等待注册页面加载完成，点击邮箱注册选项 ──
            await wait_and_click(
                tab,
                SELECTORS["email_option_btn"],
                timeout=20,
                description="邮箱注册按钮",
            )

            # ── 步骤2：等待邮箱输入框出现，填写邮箱 ──
            await wait_and_type(
                tab,
                SELECTORS["email_input"],
                email,
                description="邮箱输入框",
            )

            # ── 步骤3：点击提交邮箱 ──
            await wait_and_click(
                tab,
                SELECTORS["email_submit_btn"],
                description="邮箱提交按钮",
            )

            # ── 步骤4：等待结果——要么出现验证码输入框，要么出现错误 ──
            # 两种可能并行检测
            code_input_appeared = False
            for _ in range(DEFAULT_TIMEOUT * 2):
                # 检查是否出现了错误信息
                try:
                    page_text = await tab.get_text()
                    if "已被拒绝" in page_text or "rejected" in page_text.lower():
                        console.print("[red]邮箱域名被拒绝[/red]")
                        browser.stop()
                        return False
                except:
                    pass

                # 检查验证码输入框是否出现
                try:
                    await tab.find(SELECTORS["code_input"], timeout=0.5)
                    code_input_appeared = True
                    break
                except:
                    pass

                await asyncio.sleep(0.5)

            if not code_input_appeared:
                console.print("[red]等待验证码输入框超时[/red]")
                browser.stop()
                return False

            console.print("[green]✓ 验证码输入框已出现[/green]")

            # ── 步骤5：获取验证码 ──
            code = await extractCode(mail)
            if not code:
                console.print("[red]未获取到验证码[/red]")
                browser.stop()
                return False

            # ── 步骤6：输入验证码并提交 ──
            await wait_and_type(
                tab,
                SELECTORS["code_input"],
                code,
                description="验证码输入框",
            )
            await wait_and_click(
                tab,
                SELECTORS["code_submit_btn"],
                description="验证码提交按钮",
            )

            # ── 步骤7：等待个人信息表单出现 ──
            full_name_en = idg.fullName(locale="en")
            name_parts = full_name_en.split()
            first_name = name_parts[0]
            last_name = name_parts[-1]
            password = idg.password()

            console.print(f"[cyan]姓名:[/cyan] {first_name} {last_name}")

            # 等待 first_name 输入框出现，说明页面已切换到个人信息步骤
            await wait_and_type(
                tab,
                SELECTORS["first_name_input"],
                first_name,
                timeout=20,
                description="名字输入框",
            )
            await wait_and_type(
                tab,
                SELECTORS["last_name_input"],
                last_name,
                description="姓氏输入框",
            )
            await wait_and_type(
                tab,
                SELECTORS["password_input"],
                password,
                description="密码输入框",
            )

            # ── 步骤8：点击注册按钮，等待URL变化确认注册成功 ──
            for attempt in range(10):
                try:
                    await wait_and_click(
                        tab,
                        SELECTORS["signup_btn"],
                        timeout=5,
                        description=f"注册按钮(尝试{attempt+1})",
                    )
                    # 每次点击后检查是否离开了注册页
                    left = await wait_for_url_not_contains(tab, "sign-up", timeout=3)
                    if left:
                        console.print("[green]✓ 已离开注册页[/green]")
                        break
                except:
                    break

            # ── 步骤9：提取 token ──
            console.print("[dim]提取 cookies...[/dim]")
            cookies = await browser.cookies.get_all()
            sso = next((c.value for c in cookies if c.name == "sso"), None)

            if sso:
                saveToken(sso)
                console.print(f"[green]✓ 注册成功[/green] token: {sso[:20]}...")
                browser.stop()
                return True
            else:
                # 可能需要等一下再拿 cookie（某些场景 cookie 设置有延迟）
                console.print("[yellow]首次未找到 sso，等待重试...[/yellow]")
                await asyncio.sleep(2)
                cookies = await browser.cookies.get_all()
                sso = next((c.value for c in cookies if c.name == "sso"), None)
                if sso:
                    saveToken(sso)
                    console.print(f"[green]✓ 注册成功[/green] token: {sso[:20]}...")
                    browser.stop()
                    return True

                console.print("[red]未找到 sso token[/red]")
                browser.stop()
                return False

        except TimeoutError as e:
            console.print(f"[red]超时错误: {e}[/red]")
            browser.stop()
            return False
        except Exception as e:
            console.print(f"[red]未知错误: {e}[/red]")
            browser.stop()
            return False


async def extractCode(mail):
    """从临时邮箱中提取验证码，返回验证码字符串"""

    with Progress(
        SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("等待验证码...", total=None)

        for attempt in range(15):
            progress.update(task, description=f"检查邮箱 ({attempt + 1}/15)")

            inbox = mail.listAll()
            if not inbox:
                await asyncio.sleep(7)
                continue

            latest = inbox[0]
            mail_id = latest.get("mailID") or latest.get("messageID")
            if not mail_id:
                await asyncio.sleep(7)
                continue

            body = mail.readMessage(mail_id)
            full_text = f"{latest.get('subject', '')}\n{body}\n{latest.get('textBody', '')}\n{latest.get('htmlBody', '')}"

            code = mail.findCode(full_text)
            if code and len(str(code)) >= 5:
                console.print(f"[green]✓ 验证码:[/green] {code}")
                return code

            soup = BeautifulSoup(body or latest.get("htmlBody", ""), "html.parser")
            td = soup.find(
                "td", style=re.compile(r"font-size:\s*26px.*font-weight:\s*bold", re.I)
            )
            if td:
                code = td.get_text(strip=True)
                if len(code) >= 5:
                    console.print(f"[green]✓ 验证码:[/green] {code}")
                    return code

            match = re.search(r"\b[A-Z0-9]{3}-[A-Z0-9]{3}\b", full_text)
            if match:
                code = match.group(0)
                console.print(f"[green]✓ 验证码:[/green] {code}")
                return code

            await asyncio.sleep(7)

    return None


def saveToken(token):
    """保存 token 到 tokens.txt"""
    with open("tokens.txt", "a", encoding="utf-8") as f:
        f.write(f"{token}\n")


async def runBatch(count):
    """批量注册"""

    console.print(
        Panel.fit(
            f"[bold cyan]开始批量注册 {count} 个账号[/bold cyan]", border_style="cyan"
        )
    )

    success = 0
    failed = 0

    for i in range(count):
        console.print(f"\n[bold yellow]═══ 第 {i + 1}/{count} 个账号 ═══[/bold yellow]")

        try:
            result = await register()
            if result:
                success += 1
            else:
                failed += 1
        except Exception as e:
            console.print(f"[red]注册失败: {e}[/red]")
            failed += 1

        if i < count - 1:
            console.print("[dim]等待 3 秒后继续...[/dim]")
            await asyncio.sleep(3)

    table = Table(title="注册统计", border_style="cyan")
    table.add_column("状态", style="cyan")
    table.add_column("数量", justify="right", style="green")
    table.add_row("成功", str(success))
    table.add_row("失败", str(failed))
    table.add_row("总计", str(count))
    console.print("\n")
    console.print(table)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=ResourceWarning)

    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        console.print("[bold cyan]x.ai 批量注册工具[/bold cyan]")
        count = console.input("[cyan]请输入注册数量:[/cyan] ")

        try:
            count = int(count)
            if count <= 0:
                console.print("[red]数量必须大于 0[/red]")
                sys.exit(1)
        except ValueError:
            console.print("[red]请输入有效数字[/red]")
            sys.exit(1)

        asyncio.run(runBatch(count))

    except KeyboardInterrupt:
        console.print("\n[yellow]程序被用户中断[/yellow]")
    finally:
        # 给事件循环一点时间清理子进程资源
        import gc
        gc.collect()