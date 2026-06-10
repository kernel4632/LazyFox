#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys

sys.path.insert(0, "d:/kernyr/LazyFox")

from tools.browser import Browser


def main():
    print("=" * 70)
    print("Browser 全面测试开始")
    print("=" * 70)

    test_page1 = "http://127.0.0.1:5500/tests/browser_test_page1.html"
    test_page2 = "http://127.0.0.1:5500/tests/browser_test_page2.html"

    print(f"测试页面 1：{test_page1}")
    print(f"测试页面 2：{test_page2}")

    try:
        browser = Browser(headless=False, isDebug=True)

        print(f"\n当前URL: {browser.getUrl()}")

        print("\n正在打开测试页面1...")
        result = browser.goto(test_page1)
        print(f"goto 返回: {result}")
        print(f"打开后的URL: {browser.getUrl()}")
        print(f"页面标题: {browser.getTitle()}")

        browser.sleep(1)

        print("\n正在打开测试页面2...")
        result = browser.goto(test_page2)
        print(f"goto 返回: {result}")
        print(f"打开后的URL: {browser.getUrl()}")
        print(f"页面标题: {browser.getTitle()}")

        browser.close()
        print("\n测试完成！")

    except Exception as e:
        print(f"\n测试过程中发生错误: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
