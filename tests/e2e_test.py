"""E2E 测试：Playwright 自动化测试（真实浏览器业务流）。

用例：
1. 页面加载（无 JS 错误）          —— 前端重写回归
2. Tab 切换（6 个 tab，家庭相册已回滚）

配置（环境变量）：
  BIRDING_BASE      目标站点（缺省 http://124.223.171.149；本地 http://127.0.0.1:8765）

入口是 main()（函数名不用 test_ 前缀，避免 pytest 收集 async 函数）。
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("BIRDING_BASE", "http://124.223.171.149")


def _skip(reason):
    print(f"SKIP: {reason}")


async def _new_page(p):
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    return browser, page, errors


async def case_page_load():
    async with async_playwright() as p:
        browser, page, errors = await _new_page(p)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_timeout(2500)
        assert len(errors) == 0, f"控制台错误: {errors}"
        title = await page.title()
        assert "观鸟" in title, f"标题错误: {title}"
        # 统计卡与物种墙有渲染
        n = await page.locator("#stats .stat").count()
        assert n >= 4, f"统计卡渲染异常: {n}"
        await browser.close()
        print("✅ 页面加载测试通过（无 JS 错误，统计卡正常）")


async def case_tab_switch():
    async with async_playwright() as p:
        browser, page, _ = await _new_page(p)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_timeout(2000)
        for name, tid in [("物种墙", "wall"), ("地图", "map"), ("可视化", "viz"),
                          ("推荐", "rec"), ("不是我的鸟？", "review"),
                          ("扫描与识别", "run")]:
            await page.click(f'.tab[data-t="{tid}"]')
            await page.wait_for_timeout(900)
            display = await page.locator(f"#t-{tid}").evaluate("el => el.style.display")
            assert display == "block", f"{name} tab 未显示"
        # 家庭相册 tab 不应存在（回滚回归）
        n = await page.locator('.tab[data-t="albums"]').count()
        assert n == 0, "albums tab 应已移除"
        await browser.close()
        print("✅ Tab 切换测试通过（6/6，无 albums）")


async def main():
    print(f"=== 开始 E2E 测试（{BASE}）===")
    await case_page_load()
    await case_tab_switch()
    print("=== 所有测试通过 ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
        sys.exit(1)
