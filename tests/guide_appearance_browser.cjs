/* 仅验证回复头像：只读网络夹具，不写项目、不发送模型请求。 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
    const browser = await chromium.launch({headless: true, channel: 'msedge'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], mutations = [], pid = 'appearance-readonly-fixture';
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (request.url().includes('/api/') && request.method() !== 'GET') mutations.push(request.url());
    });
    await page.route('**/api/projects', route => route.fulfill({json: {projects: []}}));
    await page.route(`**/api/projects/${pid}/workspace`, route => route.fulfill({json: {
        project: {topic: '头像界面测试，不是真实研究项目', domain: 'ai'},
        papers: [], counts: {}, guide_mode: 'rules', starters: {overview: []},
        workspace: {project_id: pid, revision: 0, items: [], suggestions: [], messages: [
            {role: 'user', area: 'overview', content: '测试问题', questions: [], cited_paper_ids: [], suggestion_ids: []},
            {role: 'assistant', area: 'overview', content: '仅为界面测试，不是模型输出。', questions: [], cited_paper_ids: [], suggestion_ids: []},
        ]},
    }}));
    try {
        await page.goto('http://127.0.0.1:8001/workspace');
        // 旧版本选择过完整形象，刷新也不应恢复已删除的界面。
        await page.evaluate(() => localStorage.setItem('sra.guide.appearance', 'portrait'));
        await page.reload();
        await page.waitForFunction(() => document.querySelector('#research-guide-toggle').onclick);
        assert.equal(await page.locator('#rg-portrait, #rg-header-avatar, #rg-show-portrait, #rg-show-avatar').count(), 0);
        assert.equal(await page.locator('#research-guide img').count(), 0);
        await page.evaluate(projectId => window.dispatchEvent(new CustomEvent('sra:context', {detail: {projectId, page: 'dashboard'}})), pid);
        await page.locator('.rg-message.assistant .rg-avatar').waitFor();
        assert.equal(await page.locator('.rg-message.user .rg-avatar').count(), 0);
        const image = page.locator('.rg-message.assistant .rg-avatar img');
        await image.evaluate(img => img.decode());
        assert.ok(await image.evaluate(img => img.naturalWidth > 0));
        assert.equal(await page.locator('#research-guide img').count(), 1);
        await page.locator('#rg-input').fill('保留未发送的草稿');
        await page.locator('#rg-records-tab').click();
        await page.locator('#rg-chat-tab').click();
        assert.equal(await page.locator('#rg-input').inputValue(), '保留未发送的草稿');
        for (const size of [{width: 1440, height: 650}, {width: 1280, height: 800}, {width: 390, height: 844}]) {
            await page.setViewportSize(size);
            await page.locator('#rg-input').evaluate(node => node.scrollIntoView({block: 'center', behavior: 'instant'}));
            const input = await page.locator('#rg-input').boundingBox();
            assert.ok(input.x >= 0 && input.x + input.width <= size.width + 1);
            assert.ok(input.y >= 0 && input.y + input.height <= size.height + 1);
        }
        assert.deepEqual(errors, []);
        assert.deepEqual(mutations, []);
        console.log(JSON.stringify({reply_avatar_only: true,extra_portrait_removed: true,old_preference_ignored: true,draft_preserved: true,responsive_sizes: 3,errors,mutations}));
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
