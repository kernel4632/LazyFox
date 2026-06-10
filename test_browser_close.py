#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
sys.path.insert(0, 'd:/kernyr/LazyFox')

from tools.browser import Browser

def main():
    print('=' * 70)
    print('Browser 关闭测试')
    print('=' * 70)

    test_page1 = 'http://127.0.0.1:5500/browser_test_page1.html'

    try:
        browser = Browser(headless=False, isDebug=True)

        print(f'\n当前URL: {browser.getUrl()}')
        print(f'browser type: {type(browser.browser)}')
        print(f'browser has aclose: {hasattr(browser.browser, "aclose")}')

        print('\n正在打开测试页面1...')
        result = browser.goto(test_page1)
        print(f'goto 返回: {result}')
        print(f'打开后的URL: {browser.getUrl()}')
        print(f'页面标题: {browser.getTitle()}')

        print('\n准备关闭浏览器...')
        print(f'关闭前 browser type: {type(browser.browser)}')
        print(f'关闭前 browser has aclose: {hasattr(browser.browser, "aclose")}')

        browser.close()
        print('浏览器已正常关闭')

    except Exception as e:
        print(f'\n测试过程中发生错误: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()