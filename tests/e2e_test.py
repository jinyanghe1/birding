"""E2E 测试：Playwright 自动化测试。

测试场景：
1. 页面加载
2. Tab 切换
3. 创建相册
4. 邀请码生成
"""
import asyncio
from playwright.async_api import async_playwright


async def test_page_load():
    """测试页面加载。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))
        
        await page.goto('http://124.223.171.149', timeout=30000)
        await page.wait_for_timeout(2000)
        
        assert len(errors) == 0, f"控制台错误: {errors}"
        
        # 检查标题
        title = await page.title()
        assert '观鸟' in title, f"标题错误: {title}"
        
        await browser.close()
        print("✅ 页面加载测试通过")


async def test_tab_switch():
    """测试 tab 切换。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto('http://124.223.171.149', timeout=30000)
        await page.wait_for_timeout(2000)
        
        # 测试每个 tab
        tabs = ['物种墙', '地图', '可视化', '推荐', '家庭相册']
        for tab_name in tabs:
            await page.click(f'text={tab_name}')
            await page.wait_for_timeout(1000)
            
            # 检查对应的容器是否显示
            tab_map = {
                '物种墙': 'wall',
                '地图': 'map',
                '可视化': 'viz',
                '推荐': 'rec',
                '家庭相册': 'albums'
            }
            container_id = tab_map[tab_name]
            display = await page.locator(f'#{container_id}').evaluate('el => el.style.display')
            assert display == 'block', f"{tab_name} tab 未显示"
        
        await browser.close()
        print("✅ Tab 切换测试通过")


async def test_create_album():
    """测试创建相册。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto('http://124.223.171.149', timeout=30000)
        await page.wait_for_timeout(2000)
        
        # 设置 API key
        await page.evaluate('localStorage.setItem("birding_api_key", "518c82ff1cafebb2bd22499865aae94857a89f7f6331fb49c8a3dd7cdd6ce614")')
        
        # 点家庭相册 tab
        await page.click('text=家庭相册')
        await page.wait_for_timeout(2000)
        
        # 创建相册
        await page.fill('#newAlbumName', 'E2E测试相册')
        await page.click('button:has-text("创建相册")')
        await page.wait_for_timeout(2000)
        
        # 验证创建成功
        tabs = await page.locator('#albumTabs .tab').all_inner_texts()
        assert 'E2E测试相册' in tabs, f"相册创建失败，tabs: {tabs}"
        
        await browser.close()
        print("✅ 创建相册测试通过")


async def test_invite_modal():
    """测试邀请码弹窗。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto('http://124.223.171.149', timeout=30000)
        await page.wait_for_timeout(2000)
        
        # 设置 API key
        await page.evaluate('localStorage.setItem("birding_api_key", "518c82ff1cafebb2bd22499865aae94857a89f7f6331fb49c8a3dd7cdd6ce614")')
        
        # 点家庭相册 tab
        await page.click('text=家庭相册')
        await page.wait_for_timeout(2000)
        
        # 点邀请家人
        await page.click('button:has-text("邀请家人")')
        await page.wait_for_timeout(1000)
        
        # 验证弹窗显示
        modal_visible = await page.locator('#inviteModal').is_visible()
        assert modal_visible, "邀请弹窗未显示"
        
        # 验证相册选项
        albums_html = await page.locator('#inviteAlbums').inner_html()
        assert '物种墙' in albums_html, "邀请弹窗缺少物种墙选项"
        
        await browser.close()
        print("✅ 邀请码弹窗测试通过")


async def main():
    """运行所有测试。"""
    print("=== 开始 E2E 测试 ===")
    
    await test_page_load()
    await test_tab_switch()
    await test_create_album()
    await test_invite_modal()
    
    print("=== 所有测试通过 ===")


if __name__ == '__main__':
    asyncio.run(main())
