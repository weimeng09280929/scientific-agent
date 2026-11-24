// Phase A smoke on the real isolated app. No generation actions are permitted.
const {chromium} = require('playwright');
const {randomBytes} = require('node:crypto');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8125';
const files = path.resolve('.pytest_cache/real-user-acceptance-stage5/inputs');
const out = path.resolve('docs/real-user-acceptance');
const papers = [
    ['DPR-EMNLP-2020.pdf', 'Dense Passage Retrieval for Open-Domain Question Answering'],
    ['BPR-ACL-2021.pdf', 'Efficient Passage Retrieval with Hashing for Open-domain Question Answering'],
];

async function main() {
    const browser = await chromium.launch({channel: 'msedge'});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const blocked = [];
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== base || (route.request().method() === 'POST' &&
            /\/(?:messages|guide|research-tasks|run|run-topic-discovery)(?:\/|$)/.test(url.pathname))) {
            blocked.push(url.pathname);
            return route.abort();
        }
        return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(18000);
    try {
        await page.goto(base);
        await page.getByRole('button', {name: '注册新账号'}).click();
        const email = 'stage5-preflight@example.test';
        const password = randomBytes(22).toString('base64url');
        await page.locator('[name=email]').fill(email);
        await page.locator('[name=password]').fill(password);
        await page.locator('[name=confirm_password]').fill(password);
        await page.locator('#account-submit').click();
        await page.getByText(/注册成功/).waitFor();
        await page.locator('[name=email]').fill(email);
        await page.locator('[name=password]').fill(password);
        await page.getByRole('button', {name:'登录',exact:true}).click();
        await page.locator('.app').waitFor();
        // The current home creates projects on first send. Phase A calls the
        // existing creation API so upload can be tested without generation.
        const project = await page.evaluate(async () => {
            const response = await fetch('/api/projects', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({topic: '公开论文上传预检 · DPR / BPR', domain: 'ai'}),
            });
            return {status: response.status, data: await response.json()};
        });
        assert.equal(project.status, 200, JSON.stringify(project.data));
        const {project_id: pid} = project.data;
        const navigate = () => page.goto(base + '/?page=papers&project=' + pid);
        await navigate();
        const uploads = [];
        for (const [name, title] of papers) {
            const importPanel = page.locator('.paper-import');
            if ((await importPanel.getAttribute('open')) === null) await importPanel.locator(':scope > summary').click();
            const titleInput = page.getByPlaceholder('文献标题（可选，默认用文件名）');
            await titleInput.fill(title);
            await page.locator('.upload-section input[type=file]').first().setInputFiles(path.join(files, name));
            const post = page.waitForResponse(r => r.url() === base + '/api/projects/' + pid + '/upload-paper' && r.request().method() === 'POST');
            await page.getByRole('button', {name: '上传文献',exact:true}).click();
            const reply = await post;
            assert.equal(reply.status(), 200);
            const data = await reply.json();
            assert.equal(data.title, title);
            assert.equal(data.parse_mode, 'fallback');
            assert.ok(data.chunks > 5);
            uploads.push({title, file: name, status: reply.status(), paper_id: data.paper_id, mode: data.parse_mode, chunks: data.chunks});
            await page.locator('[data-paper-id]').filter({hasText: title}).first().waitFor();
        }
        await navigate();
        const card = title => page.locator('[data-paper-id]').filter({hasText: title}).first();
        for (const [,title] of papers) await card(title).waitFor();
        await page.screenshot({path: path.join(out, 'stage-a-library-desktop.png')});
        await page.setViewportSize({width: 390,height: 844});
        await card(papers[0][1]).scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(out, 'stage-a-library-390px.png')});
        assert.deepEqual(blocked, []);
        fs.writeFileSync(path.join(out, 'stage-a-upload.json'), JSON.stringify({project: pid, uploads, modelCalls: 0, externalRequests: 0},null,2));
        console.log(JSON.stringify({uploads: uploads.length, modelCalls: 0}));
    } finally {
        await context.close();
        await browser.close();
    }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
