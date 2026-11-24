const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = 'http://127.0.0.1:8113';
const output = path.resolve('docs/question-guided-paper-reading');
const birchQuote = 'The Birch algorithm uses the caption as its only input.';

async function main() {
    const browser = await chromium.launch({channel: 'msedge'});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const outside = [], cases = [], writes = [];
    await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === base) return route.continue();
        outside.push(route.request().url());
        return route.abort();
    });
    page.on('request', request => {
        if (request.method() === 'POST') writes.push(new URL(request.url()).pathname);
    });
    page.setDefaultTimeout(18000);
    const readState = async () => (await page.request.get(base + '/__entry__/state')).json();
    const fixture = await readState();
    const chat = `/api/projects/${fixture.pid}/conversations/${fixture.cid}`;
    const input = page.locator('#ch-input');
    const panel = page.locator('.ch-material-panel');
    const status = page.locator('[data-ref=error]');
    const ready = () => page.locator('[data-ref=send]:enabled').waitFor();
    const button = (name, scope = page) => scope.getByRole('button', {name, exact: true});
    const messages = async () => (await (await page.request.get(base + chat + '/messages')).json()).messages;
    const selection = async (id, checked = true) => {
        await button('查看研究进展').click();
        await panel.getByRole('tab', {name: '文献与报告'}).click();
        const item = panel.locator(`[data-paper-id="${id}"]`);
        if (!await item.evaluate(node => node.open)) await item.locator('summary').click();
        await item.getByRole('checkbox').setChecked(checked);
        await button('返回对话', panel).click();
    };
    async function answer(question, expectedId, quote, scopeLabel) {
        const assistants = page.locator('.ch-message-assistant');
        const index = await assistants.count();
        await input.fill(question);
        await button('发送').click();
        const details = assistants.nth(index).locator('.ch-paper-reading');
        await details.locator(':scope > summary').click();
        await details.getByText(quote, {exact: true}).waitFor();
        await ready();
        const call = (await readState()).calls.at(-1);
        assert.equal(call.text, question);
        assert.equal(call.context.reading_selection.single_paper, true);
        assert.deepEqual(call.context.papers.map(p => p.paper_id), [expectedId]);
        assert.deepEqual(call.context.paper_inventory.map(p => p.paper_id), [expectedId]);
        const message = (await messages()).at(-1);
        assert.deepEqual(message.reading_materials, call.context.papers);
        assert.deepEqual(message.reading_citations.map(c => [c.paper_id, c.quote]), [[expectedId, quote]]);
        assert.equal(await details.locator('[data-reading-paper]').count(), 1);
        const display = await details.innerText();
        assert.match(display, scopeLabel);
        cases.push({question, actualPapers: call.context.papers, citations: message.reading_citations, display});
        return details;
    }
    async function reject(question, reason, withAttachment = false) {
        const before = (await readState()).calls.length;
        const count = (await messages()).length;
        const written = writes.length;
        await input.fill(question);
        await button('发送').click();
        await ready();
        await status.waitFor();
        const display = await status.innerText();
        assert.match(display, reason);
        assert.equal(await input.inputValue(), question);
        assert.equal((await readState()).calls.length, before);
        assert.equal((await messages()).length, count);
        const posts = writes.slice(written);
        assert.deepEqual(posts, [withAttachment ? '/api/conversations/reading-target' : chat + '/messages']);
        if (withAttachment) {
            const file = await page.locator('[data-ref=filePending]').innerText();
            assert.match(file, /待提交资料：retained-reading\.txt/);
            cases.push({question, display, posts, generated: 0, draftPreserved: true, file});
        } else {
            cases.push({question, display, posts, generated: 0, draftPreserved: true});
        }
    }
    try {
        await page.goto(base);
        await page.locator('[name=email]').fill('backup-a@example.com');
        await page.locator('[name=password]').fill('Offline restore sample A!');
        await button('登录').click();
        await ready();
        await page.goto(base + `/?page=dashboard&project=${fixture.pid}&conversation=${fixture.cid}`);
        await ready();
        await selection(fixture.pdf_id);

        const details = await answer('只看这篇论文，Birch Caption Retrieval 的方法是什么？', 'birch', birchQuote, /作者摘要.*页码未知/s);
        await details.getByRole('button', {name: '查看来源：Birch Caption Retrieval', exact: true}).click();
        await panel.locator('.ch-located-quote').getByText(birchQuote, {exact: true}).waitFor();
        await page.screenshot({path: path.join(output, 'target-source-desktop.png')});
        await button('返回对话', panel).click();
        await answer('只看 DOI: https://doi.org/10.5555/BIRCH-CAPTION，方法是什么？', 'birch', birchQuote, /作者摘要/);
        await selection('legacy-opening');
        await answer('只读《Birch Caption Retrieval》的方法。', 'birch', birchQuote, /Birch Caption Retrieval/);
        await reject('只看这篇论文的方法。', /只勾选一篇/);
        await selection('legacy-opening', false);
        await answer('只看这篇论文的方法。', fixture.pdf_id,
            'The Cedar encoder takes a table and a caption as separate inputs.', /第 4 页/);
        await reject('只读《Unavailable Target》的方法。', /未匹配/);
        await reject('只看 DOI 10.5555/other-account 的方法。', /未匹配当前项目/);

        await page.setViewportSize({width: 390, height: 844});
        await button('导入／查看论文').click();
        const chooser = page.waitForEvent('filechooser');
        await button('添加待上传论文').click();
        await (await chooser).setFiles({
            name: 'retained-reading.txt', mimeType: 'text/plain', buffer: Buffer.from('A pending text-layer attachment for the offline case.'),
        });
        await page.locator('[data-ref=filePending]').getByText(/retained-reading.txt/).waitFor();
        await reject('只读《Twin Retrieval Study》的方法。', /匹配多篇/, true);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.screenshot({path: path.join(output, 'target-draft-390px.png')});
        await button('移除待上传文件').click();
        await answer('只读《Birch Caption Retrieval》的方法。', 'birch', birchQuote, /作者摘要/);
        const final = await readState();
        assert.deepEqual(outside, []);
        assert.equal(final.state.public_calls, 0);
        assert.equal(final.state.source_calls, 0);
        assert.equal(final.calls.length, 5);
        fs.writeFileSync(path.join(output, 'target-browser-receipts.json'),
            JSON.stringify({cases, offlineCalls: final.calls.length, externalCalls: 0}, null, 2));
        console.log(JSON.stringify({browserScenarios: cases.length, offlineCalls: final.calls.length, externalCalls: 0}));
    } finally {
        await context.close();
        await browser.close();
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
