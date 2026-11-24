/* 只拦截软件测试夹具；验证导航、轮询、空项目及旧响应，不写真实项目。 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
    const browser = await chromium.launch({headless: true, channel: 'msedge'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], unexpected = [], a = 'badge-fixture-a', b = 'badge-fixture-b';
    page.on('pageerror', error => errors.push(error.message));
    const notes = Array.from({length: 4}, (_, i) => ({note_id: `n${i}`, text: `笔记夹具${i}`, created_at: '2026-10-17T00:00:00'}));
    const recordCounts = {note: 0, question: 1, plan: 0, result: 0, finding: 0, outline: 0};
    const legacy = process.argv.includes('--legacy-status');
    let statuses = 0, delayWorkspace = false, releaseWorkspace, workspaceWaiting;
    const waitForWorkspace = new Promise(resolve => workspaceWaiting = resolve);
    const delayedWorkspace = new Promise(resolve => releaseWorkspace = resolve);
    const status = pid => ({project_id: pid, topic: pid === a ? '计数测试A' : '空项目测试B', status: 'stopped', current_stage: '', counts: {papers: 0, gaps: pid === a ? 7 : 0, ...(!legacy ? {report_gaps: pid === a ? 2 : 0} : {}), claims: 0, experiments: 0}, ...(!legacy ? {notes_count: pid === a ? notes.length : 0} : {}), node_history: [], stage_statuses: {}, pending_human: null});
    await page.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === '/api/projects') return route.fulfill({json: {projects: [a, b].map(pid => ({...status(pid), created_at: '2026-10-17T00:00:00'}))}});
        const match = url.pathname.match(/^\/api\/projects\/([^/]+)\/(.*)$/);
        if (!match) return route.fulfill({json: {}});
        const [, pid, endpoint] = match;
        if (![a, b].includes(pid)) throw new Error('Only software fixtures may be accessed');
        if (request.method() !== 'GET') {
            if (pid === a && endpoint === 'notes' && request.method() === 'POST') {
                const note = {note_id: 'new-fixture', text: request.postDataJSON().text, created_at: '2026-10-17T00:00:00'};
                notes.push(note); return route.fulfill({json: {note}});
            }
            unexpected.push(request.method() + ' ' + url.pathname);
            return route.fulfill({status: 405, json: {detail: '测试禁止此操作'}});
        }
        let body;
        if (endpoint === 'status') {statuses++; body = status(pid);}
        else if (endpoint === 'notes') body = {notes: pid === a ? notes : []};
        else if (endpoint === 'workspace') {
            if (pid === a && delayWorkspace) {workspaceWaiting(); await delayedWorkspace;}
            body = {workspace: {project_id: pid, revision: 0, items: [], messages: [], suggestions: []}, counts: pid === a ? recordCounts : {}, papers: [], guide_mode: 'rules', starters: {overview: [], literature: [], question: []}};
        }
        else if (endpoint === 'research-report') body = {report: pid === a ? {gaps: ['报告问题一', '报告问题二'], consensus: [], conflicts: []} : null};
        else if (endpoint === 'dashboard') body = status(pid);
        else if (endpoint === 'research-materials') body = {ideas: [], artifacts: []};
        else if (endpoint === 'claims') body = {claims: []};
        else body = {};
        await route.fulfill({json: body});
    });
    async function expectBadges(report, note) {
        await page.waitForFunction(({report, note}) => ['badge-report-gaps', 'badge-notes'].every((id, i) => {
            const node = document.getElementById(id);
            return node.style.display !== 'none' && node.textContent === String(i === 0 ? report : note);
        }), {report, note});
    }
    try {
        await page.goto(`http://127.0.0.1:8001/workspace?project=${a}&page=research-report`);
        await expectBadges(2, 4);
        await page.evaluate(() => {
            window.badgeChanges = [];
            const ids = ['badge-report-gaps', 'badge-notes'];
            const observer = new MutationObserver(() => {
                window.badgeChanges.push(ids.map(id => {const n = document.getElementById(id); return n.style.display === 'none' ? 'hidden' : n.textContent;}));
            });
            ids.forEach(id => observer.observe(document.getElementById(id), {attributes: true, childList: true, subtree: true}));
            window.badgeObserver = observer;
        });
        for (const id of ['notes', 'methods', 'human', 'gaps', 'progress', 'research-report']) {
            await page.locator(`#nav [data-page="${id}"]`).click();
            await expectBadges(2, 4);
        }
        const previousPolls = statuses;
        await page.waitForFunction(() => document.getElementById('badge-report-gaps').textContent === '2');
        await page.waitForTimeout(2300);
        assert.ok(statuses > previousPolls, '必须在轮询后仍稳定');
        await expectBadges(2, 4);
        const changes = await page.evaluate(() => {window.badgeObserver.disconnect(); return window.badgeChanges;});
        assert.ok(changes.every(([report, count]) => report === '2' && count === '4'), JSON.stringify(changes));

        // 新增笔记后显示实际总数，而不是侧栏最近三条的数量。
        await page.locator('#sidebar-note-input').fill('软件测试新增笔记');
        await page.locator('#sidebar-note-save').click();
        await expectBadges(2, 5);
        await page.waitForTimeout(2200);
        await expectBadges(2, 5);

        // 让 A 项目的工作台响应晚于 B 返回，不能覆盖 B 的零计数。
        delayWorkspace = true;
        await page.evaluate(pid => window.dispatchEvent(new CustomEvent('sra:records-changed', {detail: {projectId: pid}})), a);
        await waitForWorkspace;
        await page.locator('#nav [data-page="create"]').click();
        await page.locator('.project-library-row').filter({hasText: '空项目测试B'}).locator('.project-title-link').click();
        await expectBadges(0, 0);
        releaseWorkspace();
        await page.evaluate(pid => window.dispatchEvent(new CustomEvent('sra:workspace-loaded', {detail: {projectId: pid, counts: {question: 99, plan: 99, result: 99}}})), a);
        await page.locator('#nav [data-page="notes"]').click();
        await page.locator('#content').getByText('暂无笔记', {exact: true}).waitFor();
        await expectBadges(0, 0);
        await page.waitForTimeout(2200);
        await expectBadges(0, 0);
        assert.deepEqual(errors, []);
        assert.deepEqual(unexpected, []);
        console.log(JSON.stringify({legacy_status: legacy,pages: 6, polling_stable: true, note_addition: true, zero_visible: true, stale_project_ignored: true, errors, unexpected}));
    } finally {releaseWorkspace(); await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
