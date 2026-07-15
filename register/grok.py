"""
x.ai 自动注册工具

这个文件自动完成 x.ai 账号注册全流程：生成临时邮箱 → 填写注册表单 → 获取验证码 → 完成注册 → 提取 token

主要功能：
- register() 执行完整注册流程
- extractCode() 从邮件中提取验证码
- saveToken() 保存 sso token 到文件
- runBatch() 批量注册指定数量账号

调用示例：
asyncio.run(runBatch(5))  # 注册 5 个账号
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


import nodriver as uc
import sys
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


async def register():
    """执行完整注册流程，成功后保存 token 到文件，返回是否成功"""

    with TempMail() as mail:
        email = mail.generateEmail()
        console.print(f"[cyan]临时邮箱:[/cyan] {email}")

        browser = await uc.start(
            headless=False,
            # 添加以下启动参数禁用弹窗
            args=[
                "--disable-save-password-bubble",  # 禁用保存密码弹窗
                "--no-default-browser-check",  # 禁用默认浏览器检查
                "--disable-infobars",  # 禁用顶部的信息栏 (如 Sandbox 提示)
                "--disable-notifications",  # 禁用通知权限弹窗
            ],
        )

        tab = await browser.get("https://accounts.x.ai/sign-up")
        await tab.sleep(10)

        # 填写邮箱
        await (
            await tab.find("/html/body/div[2]/div/div[1]/div[2]/div/div[2]/button[1]")
        ).click()
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/input"
            )
        ).send_keys(email)
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]"
            )
        ).click()

        # 检查邮箱是否被拒绝
        try:
            error = await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/p", timeout=5
            )
            if error and "已被拒绝" in await error.text:
                console.print("[red]邮箱域名被拒绝[/red]")
                browser.stop()
                return False
        except:
            pass

        # 获取验证码
        code = await extractCode(mail)
        if not code:
            console.print("[red]未获取到验证码[/red]")
            browser.stop()
            return False

        # 输入验证码
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div/div[1]/div[4]/input"
            )
        ).send_keys(code)
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div[2]/button[1]"
            )
        ).click()
        await tab.sleep(2)

        # 填写个人信息
        full_name_en = idg.fullName(locale="en")
        name_parts = full_name_en.split()
        first_name = name_parts[0]
        last_name = name_parts[-1]
        password = idg.password()

        console.print(f"[cyan]姓名:[/cyan] {first_name} {last_name}")

        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[1]/div/input"
            )
        ).send_keys(first_name)
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[1]/div[2]/div/input"
            )
        ).send_keys(last_name)
        await (
            await tab.find(
                "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[1]/div[2]/div/input"
            )
        ).send_keys(password)

        # 完成注册
        await tab.sleep(2)
        for i in range(10):
            try:
                await (
                    await tab.find(
                        "/html/body/div[2]/div/div[1]/div[2]/div/form/div/div[3]/button[1]",
                        timeout=5,
                    )
                ).click()
                print("sign!")
                await tab.sleep(1)
                if "sign-up" not in tab.url:
                    break
            except:
                break

        # 接受服务条款
        # await tab.sleep(3)
        # try:
        #     await (
        #         await tab.find(
        #             "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div[1]/label/button"
        #         )
        #     ).click()
        #     await (
        #         await tab.find(
        #             "/html/body/div[2]/div/div[1]/div[2]/div/form/div[1]/div[2]/label/button"
        #         )
        #     ).click()
        #     await (await tab.find("继续")).click()
        #     await tab.sleep(5)
        # except:
        #     pass

        # 提取并保存 token
        print("token!")
        cookies = await browser.cookies.get_all()
        print("cookies!")
        sso = next((c.value for c in cookies if c.name == "sso"), None)
        print("sso!")

        if sso:
            saveToken(sso)
            console.print(f"[green]✓ 注册成功[/green] token: {sso[:20]}...")
            browser.stop()
            return True
        else:
            console.print("[red]未找到 sso token[/red]")
            browser.stop()
            return False


async def extractCode(mail):
    """从临时邮箱中提取验证码，返回 6 位验证码字符串"""

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

            # 方法1：使用 TempMail 自带的 findCode
            code = mail.findCode(full_text)
            if code and len(str(code)) >= 5:
                console.print(f"[green]✓ 验证码:[/green] {code}")
                return code

            # 方法2：从 HTML 中提取
            soup = BeautifulSoup(body or latest.get("htmlBody", ""), "html.parser")
            td = soup.find(
                "td", style=re.compile(r"font-size:\s*26px.*font-weight:\s*bold", re.I)
            )
            if td:
                code = td.get_text(strip=True)
                if len(code) >= 5:
                    console.print(f"[green]✓ 验证码:[/green] {code}")
                    return code

            # 方法3：正则匹配
            match = re.search(r"\b[A-Z0-9]{3}-[A-Z0-9]{3}\b", full_text)
            if match:
                code = match.group(0)
                console.print(f"[green]✓ 验证码:[/green] {code}")
                return code

            await asyncio.sleep(7)

    return None


def saveToken(token):
    """保存 token 到 tokens.txt 文件，每行一个 token"""
    with open("tokens.txt", "a", encoding="utf-8") as f:
        f.write(f"{token}\n")


async def runBatch(count):
    """批量注册指定数量的账号"""

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
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # 获取用户输入的注册数量
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
