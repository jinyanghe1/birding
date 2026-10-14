"""E2E 测试：Playwright 自动化测试（真实浏览器业务流）。

用例：
1. 页面加载（无 JS 错误）          —— 前端重写回归
2. Tab 切换（7 个 tab）
3. 登录 + 建相册 + 上传 + 缩略图加载（Cookie 鉴权）+ 删相册
4. 邀请码弹窗 + 生成 + 注册 + 成员看到相册
5. 邀请注册端点回归（/api/invites/{code}/use）

配置（环境变量）：
  BIRDING_TEST_KEY  测试用 owner API Key（缺省则跳过需鉴权用例）
  BIRDING_BASE      目标站点（缺省 http://124.223.171.149；本地 http://127.0.0.1:8765）

入口是 main()（函数名不用 test_ 前缀，避免 pytest 收集 async 函数）。
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("BIRDING_BASE", "http://124.223.171.149")
TEST_KEY = os.environ.get("BIRDING_TEST_KEY", "")


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
                          ("推荐", "rec"), ("家庭相册", "albums"),
                          ("不是我的鸟？", "review"), ("扫描与识别", "run")]:
            await page.click(f'.tab[data-t="{tid}"]')
            await page.wait_for_timeout(900)
            display = await page.locator(f"#t-{tid}").evaluate("el => el.style.display")
            assert display == "block", f"{name} tab 未显示"
        await browser.close()
        print("✅ Tab 切换测试通过（7/7）")


async def case_album_flow():
    """登录 → 建相册 → 上传 → 缩略图加载（Cookie 鉴权关键验证）→ 删相册。"""
    if not TEST_KEY:
        _skip("未设置 BIRDING_TEST_KEY")
        return
    async with async_playwright() as p:
        browser, page, errors = await _new_page(p)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_timeout(1500)

        # 登录
        await page.click("#keyBtn")
        await page.fill("#apiKeyInput", TEST_KEY)
        await page.click("#btnSaveKey")
        await page.wait_for_selector("#userBadge", state="visible", timeout=8000)
        assert await page.locator("#userBadge").is_visible(), "登录后用户徽标未显示"

        # 建相册（生产服务器较慢，等 tab 出现而非固定睡眠）
        await page.click('.tab[data-t="albums"]')
        await page.wait_for_timeout(1500)
        album_name = f"E2E{os.getpid()}"
        await page.fill("#newAlbumName", album_name)
        await page.click("#btnCreateAlbum")
        await page.wait_for_selector(
            f"#albumTabs .tab:has-text('{album_name}')", timeout=20000)
        print(f"✅ 相册创建成功（{album_name}）")

        # 选中该相册并上传
        await page.click(f"#albumTabs .tab:has-text('{album_name}')")
        await page.wait_for_timeout(800)
        await page.set_input_files("#albumFiles", {
            "name": "e2e.jpg", "mimeType": "image/jpeg",
            "buffer": _make_jpeg()})
        await page.click("#btnAlbumUpload")
        await page.wait_for_selector(".albumTile img", timeout=20000)
        # 关键：<img> 无 header，必须靠 Cookie 鉴权加载成功（轮询等图片加载完）
        await page.wait_for_function(
            "() => { const i = document.querySelector('.albumTile img');"
            " return i && i.complete && i.naturalWidth > 0; }",
            timeout=25000)
        print("✅ 相册缩略图加载成功（Cookie 鉴权生效）")

        # 删除相册（confirm + prompt 输入相册名）
        album_name_for_prompt = album_name

        async def _dialog_handler(d):
            if d.type == "prompt":
                await d.accept(album_name_for_prompt)
            else:
                await d.accept()
        page.on("dialog", _dialog_handler)
        await page.click("#btnAlbumDelete")
        await page.wait_for_selector(
            f"#albumTabs .tab:has-text('{album_name}')", state="detached", timeout=20000)

        js_errors = [e for e in errors if "favicon" not in e]
        assert not js_errors, f"控制台错误: {js_errors}"
        await browser.close()
        print(f"✅ 相册全链路测试通过（{album_name} 建/传/删）")


async def case_invite_flow():
    """邀请码弹窗 → 生成 → API 注册 → 成员登录看到相册。"""
    if not TEST_KEY:
        _skip("未设置 BIRDING_TEST_KEY")
        return
    import json
    import urllib.request

    async with async_playwright() as p:
        browser, page, _ = await _new_page(p)
        await page.goto(BASE, timeout=30000)
        await page.wait_for_timeout(1500)
        await page.click("#keyBtn")
        await page.fill("#apiKeyInput", TEST_KEY)
        await page.click("#btnSaveKey")
        await page.wait_for_selector("#userBadge", state="visible", timeout=8000)

        await page.click('.tab[data-t="albums"]')
        await page.wait_for_selector("#albumTabs .tab:has-text('物种墙')", timeout=20000)
        await page.click("button:has-text('邀请家人')")
        await page.wait_for_timeout(800)
        assert await page.locator("#inviteModal").is_visible(), "邀请弹窗未显示"
        albums_html = await page.locator("#inviteAlbums").inner_html()
        assert "物种墙" in albums_html, "邀请弹窗缺少物种墙选项"
        await page.click("#btnGenerateInvite")
        await page.wait_for_selector("#inviteResult", state="visible", timeout=20000)
        link = await page.locator("#inviteLink").text_content()
        assert "invite=" in link, f"邀请链接异常: {link}"
        code = link.split("invite=")[1].strip()
        print(f"✅ 邀请码生成成功（{code[:6]}…）")

        # 用邀请码注册（API）
        req = urllib.request.Request(
            f"{BASE}/api/invites/{code}/use",
            data=json.dumps({"display_name": f"E2E成员{os.getpid()}"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.load(r)
        mkey = j["api_key"]
        # 成员能看到相册
        req = urllib.request.Request(
            f"{BASE}/api/albums", headers={"X-API-Key": mkey}, method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            albums = json.load(r)
        assert any(a["name"] == "物种墙" for a in albums), f"成员看不到物种墙: {albums}"
        print("✅ 邀请注册 + 成员可见性测试通过")
        await browser.close()


async def case_register_endpoint():
    """邀请注册端点必须是 /api/invites/{code}/use（P1-10 回归）。"""
    import json
    import urllib.request
    req = urllib.request.Request(
        f"{BASE}/api/invites/not-a-real-code/use",
        data=json.dumps({"display_name": "测试"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("无效邀请码应返回 4xx")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 404), f"预期 400/404，实际 {e.code}"
    print("✅ 邀请注册端点测试通过（/api/invites/{code}/use）")


def _make_jpeg():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), (34, 139, 34)).save(buf, "JPEG")
    return buf.getvalue()


async def main():
    print(f"=== 开始 E2E 测试（{BASE}）===")
    await case_page_load()
    await case_tab_switch()
    await case_album_flow()
    await case_invite_flow()
    await case_register_endpoint()
    print("=== 所有测试通过 ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
        sys.exit(1)
