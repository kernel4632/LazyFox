"""
CTF r1qwq.top 解题示例：浏览器获取 Turnstile token + 纯 HTTP 提交表单。

题目地址：https://ctf.r1qwq.top
规则要点：
- 不能使用浏览器提交表单，但题目要求获取有效 cf-turnstile-response token。
- 服务端会调用 Cloudflare siteverify 校验 token。

本示例展示 LazyFox 完整工具链：
1. scan_form + scan_challenge 从页面 HTML 提取表单结构和 Turnstile sitekey
2. solve_turnstile 用浏览器打开目标站，等待 Turnstile widget 自动完成，提取 token
3. 纯 HTTP 提交表单，token 由浏览器获取但提交不经过浏览器

运行示例：
    uv run python reverse/ctf_r1qwq.py --team player-001
    uv run python reverse/ctf_r1qwq.py --team player-001 --proxy http://127.0.0.1:7890

安全边界：
只在授权靶场 ctf.r1qwq.top 上测试。浏览器只用于获取 Turnstile token，表单提交纯走 HTTP。
"""

import argparse                                             # 非交互命令行参数

from lazyfox import HTTP, Log, scan_challenge, scan_form, solve_turnstile


SITE = "https://ctf.r1qwq.top"                              # 授权靶场根地址
log = Log("ctf-r1qwq")                                     # 全流程共用同名彩色日志


# --- 提交一次完整挑战 ---
def solve(team, proxy=None, headless=False, timeout=60):
    # team：选手 ID
    # proxy：可选代理
    # headless：是否无头运行浏览器获取 token
    # timeout：等待 Turnstile token 最长秒数
    with HTTP(base=SITE, proxy=proxy, tries=2) as web:       # 复用会话，先拿页面再提交
        log.info("获取首页 HTML")
        html = web.get("/").text                             # 纯 HTTP 拿页面源码
        form = scan_form(html)                               # 从 HTML 中提取表单结构
        if not form:
            log.error("页面没有找到表单")
            return False

        log.info(f"表单：action={form['action']} method={form['method']} 字段数={len(form['fields'])}")

        challenge = scan_challenge(html)                     # 从 HTML 中提取 Turnstile 参数
        if challenge["type"] != "turnstile":
            log.error("页面没有 Turnstile 组件")
            return False

        log.info(f"Turnstile sitekey={challenge['sitekey']}")

        # 浏览器获取 token：打开目标站，等待 widget 自动完成
        log.info("正在获取 Turnstile token...")
        token = solve_turnstile(challenge["sitekey"], SITE, proxy=proxy, timeout=timeout, headless=headless)
        if not token:
            log.error("未能获取 Turnstile token")
            return False
        log.info(f"已获取 token：{token[:40]}...")

        # 构造提交字段：保留页面预填的隐藏值，加上选手输入和 Turnstile token
        fields = {}
        for field in form["fields"]:
            if field["name"] in ("team", "message"):         # 这两个由调用方提供
                continue
            fields[field["name"]] = field["value"]           # 隐藏字段和 CSRF token 原样带上
        fields["team"] = team
        fields["message"] = f"LazyFox pure HTTP submission by {team}"
        fields[challenge["token_field"]] = token             # Turnstile 回写字段名来自扫描结果

        log.info(f"提交表单到 {form['action']}")
        response = web.post(form["action"], data=fields, auth=False)  # 纯 HTTP 提交，不经过浏览器

        if response.status_code == 200:
            log.info(f"提交成功，状态码={response.status_code}")
            log.info(f"服务端响应：{response.text[:500]}")
            return True
        else:
            log.error(f"提交失败，状态码={response.status_code}")
            log.error(f"响应：{response.text[:300]}")
            return False


# --- 解析命令行参数 ---
def main(argv=None):
    parser = argparse.ArgumentParser(description="LazyFox 完成 CTF r1qwq.top 挑战")
    parser.add_argument("--team", required=True, help="选手 ID")
    parser.add_argument("--proxy", default=None, help="HTTP 代理地址")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="是否无头运行浏览器")
    parser.add_argument("--timeout", type=int, default=60, help="等待 Turnstile token 秒数")
    args = parser.parse_args(argv)

    ok = solve(args.team, args.proxy, args.headless, args.timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())                                  # 无 input()，执行结束后自然退出
