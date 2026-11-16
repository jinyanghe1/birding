"""E2E 测试：Playwright 自动化测试（真实浏览器业务流）。

用例：
1. 页面加载（无 JS 错误）          —— 前端重写回归
2. Tab 切换（6 个 tab，家庭相册已回滚）
3. 删除二次确认 + 修正自动补全     —— 写请求全部拦截，不碰真实数据
4. 地图默认常住地 + 浏览器定位
6. 全图预览：缩略图列表、按钮、原图加载、前后切换

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
        # 光有统计卡不代表物种墙正常；必须断言真实卡片已渲染。
        await page.wait_for_function("document.querySelectorAll('#grid .card').length > 0")
        cards = await page.locator("#grid .card").count()
        assert cards > 0, "物种墙不应为空"
        assert "还没有观测记录" not in await page.locator("#grid").inner_text()
        await browser.close()
        print("✅ 页面加载测试通过（无 JS 错误，统计卡正常）")


async def case_wall_error_state():
    """接口失败必须显示真实错误，不得伪装成“还没有观测记录”。"""
    async with async_playwright() as p:
        browser, page, _ = await _new_page(p)

        async def fail_species(route):
            await route.fulfill(status=500, content_type="application/json",
                                body='{"detail":"e2e forced failure"}')

        await page.route("**/api/species?min_conf=*", fail_species)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_function("document.querySelector('#grid').textContent.includes('加载失败')")
        text = await page.locator("#grid").inner_text()
        assert "还没有观测记录" not in text
        assert await page.locator("#grid button", has_text="重新加载").count() == 1
        await browser.close()
        print("✅ 物种墙接口异常提示测试通过")


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


async def case_fullsize_preview():
    """列表只用小图；详情按钮加载独立全图，支持导航和关闭。"""
    async with async_playwright() as p:
        browser, page, errors = await _new_page(p)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_function("document.querySelectorAll('#grid .card').length > 0")
        first_src = await page.locator("#grid .card .ph").first.get_attribute("src")
        assert "/full/" not in first_src, "物种墙不得直接加载全图"
        await page.locator("#grid .card").first.click()
        await page.locator("#modal .fullBtn").first.wait_for(timeout=15000)
        await page.locator("#modal .fullBtn").first.click()
        await page.wait_for_function("document.querySelector('#fullViewer')?.style.display === 'grid'")
        await page.wait_for_function("document.querySelector('#fullImage')?.complete && document.querySelector('#fullImage').naturalWidth > 0")
        src = await page.locator("#fullImage").get_attribute("src")
        kind = await page.locator("#fullKind").inner_text()
        assert "/full/" in src or "回退" in kind or "尚未同步" in kind
        if "原始分辨率" in kind:
            assert await page.locator("#fullImage").evaluate("im => im.naturalWidth") > 320
        if await page.locator("#modal .fullBtn").count() > 1:
            await page.locator("#fullNext").click()
            assert "2/" in await page.locator("#fullMeta").inner_text()
        await page.keyboard.press("Escape")
        assert await page.locator("#fullViewer").evaluate("el => el.style.display") == "none"
        assert not errors, f"控制台错误: {errors}"
        await browser.close()
        print("✅ 全图预览测试通过（列表小图、全图加载、导航、Esc 关闭）")


async def case_review_actions():
    """复核按钮真实 DOM 交互；写 API 在浏览器层拦截，真实删除由 pytest 临时库覆盖。"""
    async with async_playwright() as p:
        browser, page, errors = await _new_page(p)
        writes = []

        async def handle_delete(route):
            await _fulfill_write(route, writes, {
                "ok": True, "deleted_obs": 1, "deleted_photos": 1,
            })

        async def handle_reassign(route):
            await _fulfill_write(route, writes, {
                "ok": True, "new_species": "玄凤鹦鹉", "merged_into": False,
            })

        await page.route("**/api/review/not-bird?**", handle_delete)
        await page.route("**/api/review/reassign?**", handle_reassign)
        await page.goto(BASE, timeout=30000)
        await page.click('.tab[data-t="review"]')
        first = page.locator("#reviewList .reviewItem").first
        await first.wait_for(timeout=15000)

        # 第一次确认取消：不得发出删除请求。
        async def cancel_first(dialog):
            assert "确认删除" in dialog.message
            await dialog.dismiss()
        page.once("dialog", cancel_first)
        await first.locator("button.danger").click()
        await page.wait_for_timeout(300)
        assert not writes, "取消首次确认后不应发写请求"

        # 两次确认都接受：必须恰好出现两个对话框，且只发一次删除。
        confirms = []
        async def accept_all(dialog):
            confirms.append(dialog.message)
            await dialog.accept()
        page.on("dialog", accept_all)
        await first.locator("button.danger").click()
        await page.wait_for_function(
            "document.querySelector('#reviewList .reviewItem .acts')?.textContent.includes('已删除')")
        page.remove_listener("dialog", accept_all)
        assert len(confirms) == 2 and "再确认一次" in confirms[1]
        assert len(writes) == 1 and "review/not-bird" in writes[0]

        # 修正：输入别名 → 下拉选规范种 → 请求必须带 species_id。
        # 用第二条记录，避免上一条已删除状态影响断言。
        second = page.locator("#reviewList .reviewItem").nth(1)
        if await second.count():
            await second.locator("button", has_text="修正").click()
            await page.locator("#reassignInput").fill("玄凤")
            await page.locator(".spItem[data-i]").first.wait_for(timeout=10000)
            selected = page.locator(".spItem[data-i]").first
            assert "玄凤鹦鹉" in await selected.inner_text()
            await selected.click()
            assert await page.locator("#reassignInput").input_value() == "玄凤鹦鹉"
            await page.locator("#reassignOk").click()
            await page.wait_for_function(
                "document.querySelectorAll('#reviewList .reviewItem')[1]?.textContent.includes('已修正')")
            assert len(writes) == 2 and "species_id=" in writes[1]
        assert not errors, f"控制台错误: {errors}"
        await browser.close()
        print("✅ 删除双确认 + 修正自动补全/别名归一测试通过（未写真实库）")


async def _fulfill_write(route, writes, payload):
    writes.append(route.request.url)
    await route.fulfill(status=200, content_type="application/json",
                        body=__import__("json").dumps(payload, ensure_ascii=False))


async def case_map_location():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            geolocation={"latitude": 22.5431, "longitude": 114.0579},
            permissions=["geolocation"],
        )
        page = await context.new_page()
        await page.goto(BASE, timeout=30000)
        # 捕获 Leaflet 实例，直接断言默认中心，而不是只看按钮文案。
        await page.evaluate("""() => {
          const original = L.map;
          L.map = (...args) => { const m = original(...args); window.__e2eMap = m; return m; };
        }""")
        await page.click('.tab[data-t="map"]')
        await page.wait_for_function("window.__e2eMap && document.querySelector('#btnHome')")
        center = await page.evaluate("window.__e2eMap.getCenter()")
        assert abs(center["lat"] - 22.5431) < 0.01
        assert abs(center["lng"] - 114.0579) < 0.01
        assert await page.evaluate("window.__e2eMap.getZoom()") == 11
        await page.click("#btnLocate")
        if await page.evaluate("window.isSecureContext"):
            await page.wait_for_function("window.__e2eMap.getZoom() === 13")
            assert await page.locator(".leaflet-tooltip").filter(has_text="我的位置").count() == 1
            print("✅ 地图默认深圳 + 浏览器定位测试通过")
        else:
            await page.wait_for_function("document.querySelector('#toast')?.textContent.includes('HTTP')")
            assert await page.evaluate("window.__e2eMap.getZoom()") == 11
            print("✅ 地图默认深圳通过；公网 HTTP 定位限制提示通过")
        await browser.close()


async def case_recommendation_location():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            geolocation={"latitude": 22.5431, "longitude": 114.0579},
            permissions=["geolocation"],
        )
        page = await context.new_page()
        urls = []
        page.on("request", lambda req: urls.append(req.url) if "/api/recommendations" in req.url else None)
        await page.goto(BASE, timeout=30000)
        await page.click('.tab[data-t="rec"]')
        await page.wait_for_function("document.querySelector('#recLocLabel').textContent.includes('深圳')")
        assert await page.locator("#recCity").input_value() == "深圳"
        await page.locator("#recCity").select_option("北京")
        await page.wait_for_function("document.querySelector('#recLocLabel').textContent.includes('北京')")
        assert any("city=%E5%8C%97%E4%BA%AC" in u for u in urls), "应请求北京推荐"
        await page.select_option("#recNameType", "cn")
        await page.wait_for_timeout(300)
        cn_text = await page.locator("#recList").inner_text()
        assert any("\u4e00" <= c <= "\u9fff" for c in cn_text), "中文模式应显示中文名"
        await page.click("#recLocate")
        if await page.evaluate("window.isSecureContext"):
            await page.wait_for_function("document.querySelector('#recLocLabel').textContent.includes('50km')")
            assert any("lat=22.5431" in u and "lon=114.0579" in u for u in urls)
            print("✅ 推荐城市切换 + 浏览器定位 + 中文名测试通过")
        else:
            await page.wait_for_function("document.querySelector('#recLocLabel').textContent.includes('HTTP')")
            assert "请改用城市选择" in await page.locator("#recLocLabel").inner_text()
            print("✅ 推荐城市切换 + 中文名通过；公网 HTTP 定位提示通过")
        await browser.close()


async def main():
    print(f"=== 开始 E2E 测试（{BASE}）===")
    await case_page_load()
    await case_wall_error_state()
    await case_tab_switch()
    await case_fullsize_preview()
    await case_review_actions()
    await case_map_location()
    await case_recommendation_location()
    print("=== 所有测试通过 ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
        sys.exit(1)
