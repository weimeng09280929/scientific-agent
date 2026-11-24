const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base='http://127.0.0.1:8113';
const out=path.resolve('docs/conversation-entry-clarity');
const defer=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};

(async()=>{
    fs.mkdirSync(out,{recursive:true});
    const browser=await chromium.launch({channel:'msedge'});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage(), errors=[], writes=[], external=[], cases=[];
    page.setDefaultTimeout(18000);
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.method()!=='GET')writes.push({url:r.url(),method:r.method(),body:r.url().endsWith('/messages')?r.postDataJSON():null});});
    await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
    const state=async()=>await (await page.request.get(base+'/__entry__/state')).json();
    const s=await state();
    const chat=(pid,cid)=>base+`/?page=dashboard&project=${pid}&conversation=${cid}`;
    const workspace=pid=>page.evaluate(async pid=>(await(await fetch('/api/projects/'+pid+'/workspace?include_chat=false')).json()).workspace,pid);
    const btn=(name,scope=page)=>scope.getByRole('button',{name,exact:typeof name==='string'});
    const ready=()=>page.locator('[data-ref=send]:enabled').waitFor();
    const input=page.locator('#ch-input');
    const answer=page.locator('.ch-message-assistant');
    const panel=page.locator('.ch-material-panel');
    const formal=page.locator('[data-ref=formalProgress]');
    const discussion=page.locator('[data-ref=progress]');
    async function send(text){
        const n=await answer.count();
        if(text!==undefined)await input.fill(text);
        await btn('发送').click();
        await answer.nth(n).waitFor();
        await ready();
        return answer.nth(n);
    }
    async function login(email,password){
        await page.locator('[name=email]').fill(email);
        await page.locator('[name=password]').fill(password);
        await btn('登录').click();
    }
    try{
        await page.goto(base);
        await login('backup-a@example.com','Offline restore sample A!');
        await ready();
        await page.goto(chat(s.pid,s.cid));
        await ready();
        const first=await send('请解释如何做公平的比较。');
        assert.match(await formal.innerText(),/研究问题尚未确认/);
        assert.equal(await discussion.evaluate(n=>n.tagName),'P');
        assert.equal(await page.locator('#topbar-status').isVisible(),false);
        assert.equal(await page.locator('.ch-action-options,.ch-route-next,[data-ref=beginResearchStep]').count(),0);
        assert.equal(await first.locator('.ch-reply-option small').isVisible(),true);
        assert.equal(await first.locator('.ch-option-content').isVisible(),true);
        cases.push('ordinary answer can be sent immediately; recommendations and literal payload are visible');

        for(const text of ['没听懂','还是没听懂，再简单一点','先暂停一下']){
            const m=await send(text);
            assert.equal(await m.locator('.ch-reply-option,[data-action-type],.ch-question-card').count(),0);
        }
        assert((await state()).calls.at(-2).context.response_preferences.needs_reexplanation);
        cases.push('consecutive confusion and pause suppress progression through the real response policy');

        await input.fill('我自己的草稿仍要继续修改');
        await btn('导入／查看论文').click();
        const chooser=page.waitForEvent('filechooser');
        await btn('添加待上传论文').click();
        await (await chooser).setFiles({name:'isolated.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n')});
        await page.locator('[data-ref=filePending]').getByText(/isolated\.pdf/).waitFor();
        const callsBefore=(await state()).calls.length;
        await first.locator('.ch-reply-option').click();
        await btn('保留草稿，暂不发送').click();
        assert.equal((await state()).calls.length,callsBefore);
        assert.equal(await input.inputValue(),'我自己的草稿仍要继续修改');
        await first.locator('.ch-reply-option').click();
        const payload=page.waitForRequest(r=>r.method()==='POST' && r.url().endsWith('/messages'));
        await btn('保留草稿并发送推荐').click();
        assert.equal((await payload).postDataJSON().content,'请举例解释对照的用途。');
        await answer.nth(callsBefore).waitFor();
        await ready();
        assert.equal((await state()).calls.length,callsBefore+1);
        assert.equal(await input.inputValue(),'我自己的草稿仍要继续修改');
        assert.match(await page.locator('[data-ref=filePending]').innerText(),/isolated\.pdf/);
        assert.equal(writes.filter(r=>r.url.endsWith('/upload-paper')).length,0);
        const remove=page.locator('[data-ref=filePending] button').first();
        await remove.click();
        await input.fill('');
        cases.push('recommendation cancel and confirm preserve text and attachment; exact content sent once without upload');

        const readOnlyBefore=writes.length;
        await first.locator('.ch-citation').click();
        await panel.locator('[data-paper-id=abstract-only][open]').waitFor();
        await page.keyboard.press('Escape');
        await panel.waitFor({state:'hidden'});
        await page.locator('.ch-settings>summary').focus();
        await page.keyboard.press('Enter');
        await page.locator('.ch-route-details>summary').click();
        await page.locator('[data-research-step=design]').click();
        assert.match(await discussion.innerText(),/研究方法/);
        assert.match(await formal.innerText(),/研究问题尚未确认/);
        assert.equal(await input.inputValue(),'');
        assert.equal(writes.length,readOnlyBefore);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.ch-settings>summary').evaluate(el=>el===document.activeElement),true);
        const method=await send('只讨论怎样比较两种方法，不要生成研究方案。');
        assert.equal(await method.locator('[data-plan-preview-id]').count(),0);
        assert.equal((await state()).calls.at(-1).context.conversation.discussion_step,'design');
        assert.equal((await workspace(s.pid)).items.length,0);
        cases.push('citation, settings and local discussion choice are read-only; method discussion does not create records');

        const candidates=await send('给出两个候选供我审阅。');
        const q=candidates.locator('[data-question-suggestion]');
        assert.equal(await q.count(),2);
        assert.equal(await q.locator('.ch-card-primary').count(),0);
        assert.match(await q.nth(0).innerText(),/候选甲/);
        assert.match(await q.nth(1).innerText(),/候选乙/);
        await q.nth(1).locator('summary').click();
        assert.match(await q.nth(1).innerText(),/相同测试条件/);
        await q.nth(1).scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'candidates-desktop.png')});
        await page.setViewportSize({width:390,height:844});
        await q.nth(1).scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(out,'candidates-390px.png')});
        await page.setViewportSize({width:1440,height:1000});
        const sid=await q.nth(1).getAttribute('data-question-suggestion');
        const confirmed=page.waitForRequest(r=>r.method()==='POST' && r.url().endsWith('/suggestions/'+sid+'/apply'));
        await btn(/^确认这个研究问题/,q.nth(1)).click();
        await confirmed;
        await q.nth(1).getByText('已确认',{exact:true}).waitFor();
        const record=(await workspace(s.pid)).items.find(i=>i.kind==='question');
        assert.match(record.title,/候选乙/);
        assert.match(await formal.innerText(),/研究问题已确认/);
        assert.equal(await btn(/^准备方法草案请求/,q.nth(1)).count(),1);
        cases.push('two bound candidate cards; confirming B writes only B and visibly updates formal state');

        const prepare=btn(/^准备方法草案请求/,q.nth(1));
        await prepare.click();
        assert.equal(await page.locator('[data-ref=planQuestion]').inputValue(),record.item_id);
        await page.keyboard.press('Escape');
        assert.equal(await prepare.evaluate(el=>el===document.activeElement),true);
        await prepare.click();
        await page.locator('[data-ref=planMethod]').selectOption('computational');
        await btn('准备生成方案请求').click();
        assert.match(await input.inputValue(),/生成一份研究方案/);
        const draft=await send();
        const preview=draft.locator('[data-plan-preview-id]');
        await preview.waitFor();
        assert.equal((await workspace(s.pid)).items.filter(i=>i.kind==='plan').length,0);
        assert.match(await formal.innerText(),/当前方案尚未采纳/);
        const planId=await preview.getAttribute('data-plan-preview-id');
        await btn('打开方案',preview).click();
        const review=panel.locator(`[data-plan-id="${planId}"]`);
        await review.locator('summary').first().waitFor();
        assert.match(await review.innerText(),/候选乙/);
        await btn('采纳为正式方案',review).scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'plan-review-desktop.png')});
        await btn('采纳为正式方案',review).click();
        await page.waitForFunction(()=>document.querySelector('[data-ref=formalProgress]').textContent.includes('方案已采纳'));
        const adopted=(await workspace(s.pid)).items.find(i=>i.kind==='plan');
        assert.equal(adopted.plan.question_id,record.item_id);
        await btn('返回对话',panel).click();
        cases.push('confirmed question opens bound plan setup; generation saves only a draft, explicit adoption saves formal version');

        const later=await send('重新给出候选供我审阅。');
        assert.match(await later.locator('.ch-question-card').innerText(),/尚未确认/);
        assert.equal((await workspace(s.pid)).items.filter(i=>i.kind==='question').length,1);
        const before=(await workspace(s.pid)).revision;
        const status=await page.evaluate(async ({pid,before})=>(await fetch(`/api/projects/${pid}/workspace/brief`,{
            method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:before,brief:{resources:'改为仅有 CPU'}})
        })).status,{pid:s.pid,before});
        assert.equal(status,200);
        await page.reload();
        await ready();
        assert.match(await formal.innerText(),/研究问题待复核.*方案待复核/);
        assert.equal(await btn(/^准备方法草案请求/).count(),0);
        assert.equal(await btn(/^确认这个研究问题/).count(),0);
        await btn('打开方案',preview).click();
        const saved=panel.locator(`[data-plan-id="${adopted.item_id}"][open]`);
        await saved.waitFor();
        assert.equal(await btn(/采纳为正式方案|设为当前方案|请求修订方案/,saved).count(),0);
        await btn('返回对话',panel).click();
        const refused=await page.evaluate(async ({pid,cid,sid,before})=>(await fetch(`/api/projects/${pid}/conversations/${cid}/suggestions/${sid}/apply`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:before})
        })).status,{pid:s.pid,cid:s.cid,sid:await later.locator('[data-question-suggestion]').getAttribute('data-question-suggestion'),before});
        assert.equal(refused,409);
        cases.push('new candidate does not replace a formal question; changed basis hides stale actions and backend rejects adoption');

        await page.locator('[data-ref=panelToggle]').focus();
        await page.keyboard.press('Enter');
        await panel.waitFor();
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('[data-ref=panelToggle]').evaluate(el=>el===document.activeElement),true);
        await page.goto(chat(s.target,s.other));
        await ready();
        const empty=await send('先看看有哪些资料可以补充。');
        const search=empty.locator('[data-action-type=start_search]');
        await search.waitFor();
        const taskPosts=()=>writes.filter(r=>r.url.endsWith('/research-tasks') && r.method==='POST').length;
        const taskBefore=taskPosts();
        await search.click();
        await page.getByRole('dialog',{name:'确认检索范围'}).waitFor();
        assert.equal(taskPosts(),taskBefore);
        await btn('取消',page.locator('.ch-search-scope')).click();
        assert.equal(taskPosts(),taskBefore);
        assert.equal(await search.evaluate(el=>el===document.activeElement),true);
        await page.goBack();
        await ready();
        assert.match(await formal.innerText(),/待复核/);
        await page.goForward();
        await ready();
        assert.match(await formal.innerText(),/尚未确认/);
        cases.push('search scope and cancel have no task side effects; back and forward restore scoped statuses');

        await page.goto(chat(s.pid,s.cid));
        await ready();
        await answer.first().locator('.ch-reply-option').scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'desktop.png')});
        await page.setViewportSize({width:390,height:844});
        await answer.first().locator('.ch-reply-option').scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'390px.png')});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        assert(await answer.first().locator('.ch-option-content').isVisible());
        cases.push('desktop and 390px show visible reply payload and separate states without horizontal overflow');

        const held=defer(),release=defer(),finished=defer();
        await page.route('**/workspace?include_chat=false',async route=>{
            const response=await route.fetch();
            held.resolve();await release.promise;
            await route.fulfill({response}).catch(()=>{});
            finished.resolve();
        },{times:1});
        await page.locator('[data-ref=panelToggle]').click();
        await held.promise;
        await page.locator('#account-logout').evaluate(n=>n.click());
        await login('backup-b@example.com','Offline restore sample B!');
        await ready();
        release.resolve();
        await finished.promise;
        assert.equal(await page.locator('[data-question-suggestion],[data-plan-preview-id]').count(),0);
        const foreign=await page.evaluate(async pid=>(await fetch('/api/projects/'+pid+'/workspace?include_chat=false')).status,s.pid);
        assert.equal(foreign,404);
        cases.push('late workspace read after account switch cannot restore old cards; foreign project returns 404');
        const final=await state();
        assert.equal(final.state.public_calls,0);
        assert.equal(final.state.source_calls,0);
        assert.deepEqual(external,[]);
        assert.deepEqual(errors,[]);
        fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({cases,offlineProviderCalls:final.calls.length,realExternalCalls:0,errors},null,2));
        console.log(JSON.stringify({browserScenarios:cases.length,offlineProviderCalls:final.calls.length,realExternalCalls:0}));
    }catch(e){
        await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});
        throw e;
    }finally{
        await context.close();
        await browser.close();
    }
})().catch(e=>{console.error(e);process.exitCode=1;});
