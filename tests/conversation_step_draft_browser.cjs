const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8113';
const out = path.resolve('docs/conversation-entry-clarity');
const defer = () => {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return {promise, resolve};
};

async function main() {
    fs.mkdirSync(out, {recursive:true});
    const browser = await chromium.launch({channel:'msedge'});
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const writes = [], errors = [], outside = [], results = [];
    await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === base) return route.continue();
        outside.push(route.request().url());
        return route.abort();
    });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => {
        if (req.url().endsWith('/upload-paper') && req.method()==='POST') {
            writes.push({url:req.url(), method:req.method()});
        } else if (req.url().includes('/conversations/') && ['PATCH','POST'].includes(req.method())) {
            writes.push({url:req.url(), method:req.method(), body:req.postDataJSON()});
        }
    });
    const state = async () => (await page.request.get(base+'/__entry__/state')).json();
    const s = await state();
    const conversation = (pid,cid) => '/api/projects/'+pid+'/conversations/'+cid;
    const own = conversation(s.pid,s.cid);
    const get = url => page.evaluate(async url => (await fetch(url)).json(),url);
    const saved = async () => (await get(own)).conversation;
    const btn = name => page.getByRole('button',{name,exact:true});
    const input = page.locator('#ch-input');
    const progress = page.locator('[data-ref=progress]');
    const ready = () => page.locator('[data-ref=send]:enabled').waitFor();
    const route = (pid,cid) => ({projectId:pid,conversationId:cid,page:'dashboard'});
    const url = (pid,cid) => base+'/?page=dashboard&project='+pid+'&conversation='+cid;
    const go = async (pid,cid) => {
        await page.evaluate(r => window.__sraApp.navigate(r),route(pid,cid));
        await ready();
    };
    async function login(index) {
        await page.locator('[name=email]').fill('backup-'+(index ? 'b':'a')+'@example.com');
        await page.locator('[name=password]').fill('Offline restore sample '+(index ? 'B':'A')+'!');
        await btn('登录').click();
        await ready();
    }
    async function choose(step,{keyboard=false,keepOpen=false}={}) {
        const settings = page.locator('.ch-settings');
        if (!await settings.evaluate(el => el.open)) await settings.locator('summary').first().click();
        const choices = settings.locator('.ch-route-details');
        if (!await choices.evaluate(el => el.open)) await choices.locator('summary').click();
        const option = choices.locator('[data-research-step='+step+']');
        if (keyboard) { await option.focus(); await page.keyboard.press('Enter'); }
        else await option.click();
        assert.equal(await option.getAttribute('aria-pressed'),'true');
        if (!keepOpen) await page.keyboard.press('Escape');
    }
    async function attach() {
        await btn('导入／查看论文').click();
        const chooser = page.waitForEvent('filechooser');
        await btn('添加待上传论文').click();
        await (await chooser).setFiles({
            name:'step-draft.pdf', mimeType:'application/pdf',
            buffer:Buffer.from('JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCA3MiA3Ml0gPj4KZW5kb2JqCnhyZWYKMCA0CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAwOSAwMDAwMCBuIAowMDAwMDAwMDU4IDAwMDAwIG4gCjAwMDAwMDAxMTUgMDAwMDAgbiAKdHJhaWxlcgo8PCAvUm9vdCAxIDAgUiAvU2l6ZSA0ID4+CnN0YXJ0eHJlZgoxOTkKJSVFT0YK','base64'),
        });
        await page.locator('[data-ref=filePending]').getByText(/step-draft\.pdf/).waitFor();
    }
    const heading = async expected => assert.match(await progress.innerText(),new RegExp(expected));
    const reload = async () => { await page.reload(); await ready(); };
    const calls = async () => (await state()).calls;
    const work = async () => (await get('/api/projects/'+s.pid+'/workspace?include_chat=false')).workspace;
    try {
        await page.goto(base);
        await login(0);
        await page.goto(url(s.pid,s.cid));
        await ready();
        const initial = await saved();
        const baseline = await work();
        await choose('design');
        await heading('研究方法');
        assert.equal((await saved()).discussion_step,initial.discussion_step);
        assert.equal(writes.length,0);
        await reload();
        if (process.argv.includes('--reproduce')) {
            const after = await progress.innerText();
            assert.doesNotMatch(after,/研究方法/);
            fs.writeFileSync(path.join(out,'step-draft-before.json'),JSON.stringify({
                selected:'design', afterRefresh:after, serverStep:initial.discussion_step ?? null, writes:writes.length,
            },null,2));
            console.log('Reproduced: the selection was lost on reload without a write.');
            return;
        }
        await heading('研究方法');
        assert.equal(writes.length,0);
        results.push('direct reload restores selected step without PATCH or send');

        await input.fill('原文字');
        await go(s.pid,s.peer);
        await heading('论文与资料');
        assert.equal(await input.inputValue(),'');
        await go(s.pid,s.cid);
        await heading('研究方法');
        await go(s.target,s.other);
        await heading('兴趣与条件');
        await page.goBack();
        await ready();
        await heading('研究方法');
        assert.equal(await input.inputValue(),'原文字');
        await input.fill('编辑后的原文字，继续讨论对照。');
        await reload();
        assert.equal(await input.inputValue(),'编辑后的原文字，继续讨论对照。');
        await heading('研究方法');
        assert.equal(writes.length,0);
        results.push('navigate back and edit/reload preserve independent step and text drafts');

        await attach();
        await go(s.target,s.other);
        await page.goBack();
        await ready();
        await heading('研究方法');
        assert.match(await page.locator('[data-ref=filePending]').innerText(),/step-draft\.pdf/);
        await page.route(base+own,route => route.request().method()==='PATCH'
            ? route.fulfill({status:503,json:{detail:'离线夹具：设置暂不可保存'}}) : route.fallback(),{times:1});
        const failedPatch = page.waitForResponse(r => r.url()===base+own && r.request().method()==='PATCH');
        await btn('发送').click();
        assert.equal((await failedPatch).status(),503);
        await page.getByText('离线夹具：设置暂不可保存',{exact:true}).waitFor();
        assert.equal(writes.filter(w => w.method==='POST').length,0);
        assert.equal((await calls()).length,0);
        assert.equal(await input.inputValue(),'编辑后的原文字，继续讨论对照。');
        await heading('研究方法');
        assert.match(await page.locator('[data-ref=filePending]').innerText(),/step-draft\.pdf/);
        await page.screenshot({path:path.join(out,'step-draft-save-failure-desktop.png')});
        results.push('PATCH rejection sends neither upload nor message and preserves text, file and selection');

        await page.setViewportSize({width:390,height:844});
        await reload();
        await heading('研究方法');
        assert.equal(await input.inputValue(),'编辑后的原文字，继续讨论对照。');
        assert.match(await page.locator('[data-ref=filePending]').innerText(),/step-draft\.pdf/);
        await attach(); // A full reload requires reselecting the local file.
        await page.screenshot({path:path.join(out,'step-draft-restored-390px.png')});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth>innerWidth),false);
        const patch = page.waitForResponse(r => r.url()===base+own && r.request().method()==='PATCH');
        const upload = page.waitForResponse(r => r.url().endsWith('/upload-paper') && r.request().method()==='POST');
        const sent = page.waitForRequest(r => r.url()===base+own+'/messages' && r.method()==='POST');
        await btn('发送').click();
        assert.equal((await patch).request().postDataJSON().discussion_step,'design');
        const uploaded = await upload;
        assert(uploaded.ok());
        const paper = (await uploaded.json()).paper_id;
        const payload = (await sent).postDataJSON();
        assert.equal(payload.content,'编辑后的原文字，继续讨论对照。');
        assert(payload.selected_paper_ids.includes(paper));
        await page.locator('.ch-message-assistant').waitFor();
        await ready();
        assert.equal((await saved()).discussion_step,'design');
        assert.equal((await calls()).at(-1).context.conversation.discussion_step,'design');
        assert.deepEqual((await work()).items,baseline.items);
        results.push('recovered text and reselected attachment send through real PATCH, upload and message APIs');

        await choose('question');
        const retryText='保留这条未成功提交的讨论。';
        await input.fill(retryText);
        const messages = base+own+'/messages';
        await page.route(messages,route => route.request().method()==='POST'
            ? route.fulfill({status:429,json:{reason_code:'account_busy',detail:'离线夹具：账号繁忙'}}) : route.fallback(),{times:1});
        const rejected = page.waitForResponse(r => r.url()===messages && r.request().method()==='POST');
        await btn('发送').click();
        const rejection = await rejected;
        assert.equal(rejection.status(),429);
        const original = rejection.request().postDataJSON();
        await page.getByText('离线夹具：账号繁忙',{exact:true}).first().waitFor();
        assert.equal((await calls()).length,1);
        assert.equal(await input.inputValue(),retryText);
        await reload();
        await heading('研究问题');
        assert.equal(await input.inputValue(),retryText);
        const retried = page.waitForRequest(r => r.url()===messages && r.method()==='POST');
        await btn('发送').click();
        assert.deepEqual((await retried).postDataJSON(),original);
        await page.locator('.ch-message-assistant').nth(1).waitFor();
        await ready();
        assert.equal((await calls()).at(-1).context.conversation.discussion_step,'question');
        assert.equal((await calls()).at(-1).text,retryText);
        results.push('message rejection retains input, saved choice and original request identity through reload and retry');

        await choose('reading',{keyboard:true});
        const held = defer(), release = defer(), delivered = defer();
        await page.route(base+own,async intercept => {
            if (intercept.request().method()!=='PATCH') return intercept.fallback();
            const response = await intercept.fetch();
            held.resolve(response.status());
            await release.promise;
            await intercept.fulfill({response}).catch(() => {});
            delivered.resolve();
        });
        const settings = page.locator('.ch-settings');
        await settings.locator('summary').first().click();
        await btn('保存讨论设置').click();
        assert.equal(await held.promise,200);
        await choose('synthesis',{keyboard:true});
        release.resolve();
        await delivered.promise;
        await ready();
        await page.unroute(base+own);
        await heading('研究现状与缺口');
        assert.equal((await saved()).discussion_step,'reading');
        await reload();
        await heading('研究现状与缺口');
        results.push('an older acknowledged PATCH cannot discard a newer explicit choice');

        await page.evaluate(async own => {
            const r = await fetch(own,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({discussion_step:'question'})});
            if (!r.ok) throw new Error('Isolated server setting update failed');
        },own);
        await reload();
        await heading('研究现状与缺口');
        await page.locator('.ch-settings>summary').click();
        const committed = page.waitForResponse(r => r.url()===base+own && r.request().method()==='PATCH');
        await btn('保存讨论设置').click();
        assert.equal((await committed).status(),200);
        await ready();
        await page.evaluate(async own => {
            const r = await fetch(own,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({discussion_step:'question'})});
            if (!r.ok) throw new Error('Isolated server setting update failed');
        },own);
        await reload();
        await heading('研究问题');
        results.push('server wins over a committed local draft, but not over an unsaved selection');

        await choose('exploration');
        const routeHeld=defer(), routeRelease=defer(), routeFinished=defer();
        await page.route(base+own+'/research-route',async intercept => {
            const response=await intercept.fetch();
            routeHeld.resolve();
            await routeRelease.promise;
            await intercept.fulfill({response}).catch(() => {});
            routeFinished.resolve();
        },{times:1});
        await page.evaluate(r => window.__sraApp.navigate(r),route(s.target,s.other));
        await ready();
        await page.evaluate(r => window.__sraApp.navigate(r),route(s.pid,s.cid));
        await routeHeld.promise;
        await page.evaluate(r => window.__sraApp.navigate(r),route(s.target,s.other));
        await ready();
        routeRelease.resolve();
        await routeFinished.promise;
        await heading('兴趣与条件');
        await page.locator('#account-logout').evaluate(el => el.click());
        await login(1);
        await go(s.foreign,s.foreign_cid);
        await heading('研究问题');
        assert.equal(await input.inputValue(),'');
        assert.equal(await page.evaluate(async own => (await fetch(own)).status,own),404);
        await page.locator('#account-logout').evaluate(el => el.click());
        await login(0);
        await go(s.pid,s.cid);
        await heading('选题探索');
        assert.equal((await saved()).discussion_step,'question');
        results.push('late route response and account/dialog switches keep the unsent choice scoped');
        const after=await work();
        for (const field of ['items','brief','focus']) assert.deepEqual(after[field],baseline[field]);
        assert.equal((await calls()).length,2);
        assert.equal((await state()).state.public_calls,0);
        assert.deepEqual(errors,[]);
        assert.deepEqual(outside,[]);
        fs.writeFileSync(path.join(out,'step-draft-browser-results.json'),JSON.stringify({
            results, offlineProviderCalls:(await calls()).length, realExternalCalls:0,
        },null,2));
        console.log(JSON.stringify({scenarios:results.length, offlineProviderCalls:2, realExternalCalls:0}));
    } finally {
        await context.close();
        await browser.close();
    }
}
main().catch(error => { console.error(error); process.exitCode=1; });
