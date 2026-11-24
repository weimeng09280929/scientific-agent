const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base='http://127.0.0.1:8113',out=path.resolve('docs/question-guided-paper-reading');
const defer=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const method='The Cedar encoder takes a table and a caption as separate inputs.';

(async()=>{
    fs.mkdirSync(out,{recursive:true});
    const browser=await chromium.launch({channel:'msedge'});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage(),cases=[],errors=[],outside=[],writes=[];
    page.setDefaultTimeout(18000);
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.method()!=='GET'&&r.url().includes('/api/projects/'))writes.push(r.url());});
    await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(r.request().url()),r.abort()));
    const state=async()=>(await page.request.get(base+'/__entry__/state')).json();
    const s=await state();
    const chat=(pid,cid)=>base+`/?page=dashboard&project=${pid}&conversation=${cid}`;
    const ready=()=>page.locator('[data-ref=send]:enabled').waitFor();
    const panel=page.locator('.ch-material-panel');
    const btn=(name,scope=page)=>scope.getByRole('button',{name,exact:typeof name==='string'});
    const messages=page.locator('.ch-message-assistant');
    const ask=async text=>{
        const n=await messages.count();
        await page.locator('#ch-input').fill(text);
        await btn('发送').click();
        await messages.nth(n).waitFor();
        await ready();
        return messages.nth(n);
    };
    async function select(ids){
        await btn('查看研究进展').click();
        await panel.getByRole('tab',{name:'文献与报告'}).click();
        for(const id of ids){
            const item=panel.locator(`[data-paper-id="${id}"]`);
            if(!await item.evaluate(n=>n.open))await item.locator('summary').click();
            await item.getByRole('checkbox').check();
        }
        await btn('返回对话',panel).click();
    }
    try{
        await page.goto(base);
        await page.locator('[name=email]').fill('backup-a@example.com');
        await page.locator('[name=password]').fill('Offline restore sample A!');
        await btn('登录').click();
        await ready();
        await page.goto(chat(s.pid,s.cid));
        await ready();
        await select([s.pdf_id]);
        const first=await ask('只看这篇论文，Cedar 方法的输入和输出是什么？');
        const details=first.locator('.ch-paper-reading');
        await details.locator(':scope > summary').focus();
        await page.keyboard.press('Enter');
        await details.getByText(method,{exact:true}).waitFor();
        assert.match(await details.innerText(),/第 4 页/);
        const trace=(await state()).calls[0];
        assert.deepEqual(trace.context.papers.map(p=>p.paper_id),[s.pdf_id]);
        const excerpt=trace.context.papers[0].excerpts.find(e=>e.text.includes(method));
        assert.equal(excerpt.page,4);
        assert(trace.system.includes('【按问题阅读论文】'));
        const before=writes.length;
        const origin=details.getByRole('button',{name:'查看来源：Cedar Table Retrieval',exact:true});
        await origin.focus();
        await page.keyboard.press('Enter');
        const located=panel.locator('.ch-located-quote');
        await located.waitFor();
        assert.equal(await located.getAttribute('data-excerpt-id'),excerpt.excerpt_id);
        assert.match(await located.innerText(),/第 4 页.*Cedar encoder/s);
        assert.match(await located.innerText(),/未打开 PDF 原页/);
        assert.equal(writes.length,before);
        await page.screenshot({path:path.join(out,'source-desktop.png')});
        await btn('返回对话',panel).click();
        cases.push('real local PDF page 4 becomes final model input and source opens the verified snapshot, without download or writes');

        await page.reload();await ready();
        await page.setViewportSize({width:390,height:844});
        await details.locator(':scope > summary').focus();
        await page.keyboard.press('Enter');
        await details.getByText(method,{exact:true}).waitFor();
        const showInput=details.getByText('展开本轮提供的原文',{exact:true});
        await showInput.focus();await page.keyboard.press('Enter');
        const inputQuote=details.getByText(excerpt.text,{exact:true});
        await inputQuote.scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(out,'evidence-390px.png')});
        cases.push('refresh restores exact range and full selected excerpt; 390px and keyboard remain usable');

        await select(['birch']);
        const compared=await ask('比较这两篇论文的 Cedar 和 Birch method 输入。');
        const scope=compared.locator('.ch-paper-reading');
        await scope.locator(':scope > summary').click();
        assert.equal(await scope.locator('[data-reading-paper]').count(),2);
        assert.match(await scope.locator('[data-reading-paper=birch]').innerText(),/页码未知/);
        assert.match(await scope.locator('[data-reading-paper=birch]').innerText(),/作者摘要/);
        cases.push('two-paper comparison keeps scopes and quotes separate, and reports unknown page');

        assert((await page.request.post(base+'/__entry__/replace-reading-text')).ok());
        await btn('查看研究进展').click();
        await btn('返回对话',panel).click();
        await details.getByText(/资料或项目记录已更新/).waitFor({state:'attached'});
        assert.equal(await details.getByText(method,{exact:true}).count(),1);
        const reread=await ask('只看这篇论文 Cedar Table Retrieval 的 Cedar method。');
        const newScope=reread.locator('.ch-paper-reading');
        assert.equal(await newScope.getByText(method,{exact:true}).count(),0);
        assert.equal((await state()).calls.at(-1).context.papers.length,1);
        cases.push('changed stored text invalidates currentness but old quote is retained, and new input excludes it');

        const held=defer(),release=defer(),done=defer();
        await page.route('**/guidance-actions?*',async r=>{
            const response=await r.fetch();
            held.resolve();await release.promise;
            await r.fulfill({response}).catch(()=>{});
            done.resolve();
        },{times:1});
        await btn('查看研究进展').click();
        await held.promise;
        await page.evaluate(r=>window.__sraApp.navigate(r),{projectId:s.target,conversationId:s.other,page:'dashboard'});
        await ready();
        release.resolve();await done.promise;
        assert.equal(await page.locator('.ch-paper-reading,.ch-located-quote').count(),0);
        await page.goBack();await ready();
        assert.equal(await page.locator('.ch-paper-reading').count(),3);
        cases.push('late reading-state response cannot overwrite another dialog; back restores scoped snapshots');
        assert.deepEqual(errors,[]);
        assert.deepEqual(outside,[]);
        const final=await state();
        assert.equal(final.state.public_calls,0);
        assert.equal(final.state.source_calls,0);
        fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({cases,offlineCalls:final.calls.length,externalCalls:0},null,2));
        fs.writeFileSync(path.join(out,'actual-provider-input.json'),JSON.stringify(final.calls.map(c=>({
            question:c.text,policy:c.system.includes('【按问题阅读论文】'),inputChars:c.system.length+c.prompt.length,
            outputBudget:c.max_tokens_override,selection:c.context.reading_selection,papers:c.context.papers,
        })),null,2));
        const delivered=await page.evaluate(async ({pid,cid})=>(await (await fetch(
            '/api/projects/'+pid+'/conversations/'+cid+'/messages')).json()).messages,{pid:s.pid,cid:s.cid});
        fs.writeFileSync(path.join(out,'browser-citation-receipts.json'),JSON.stringify(delivered.filter(m=>m.reading_materials?.length).map(m=>({
            messageId:m.message_id,citations:m.reading_citations,basisChanged:m.reading_basis_changed,
        })),null,2));
        console.log(JSON.stringify({browserScenarios:cases.length,offlineCalls:final.calls.length,externalCalls:0}));
    }catch(e){await page.screenshot({path:path.join(out,'browser-failure.png')}).catch(()=>{});throw e;}
    finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
