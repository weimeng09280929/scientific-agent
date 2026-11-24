const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8113';
const out = path.resolve('docs/research-gap-guidance');
const defer = () => { let resolve; const promise = new Promise(r=>{resolve=r;}); return {promise,resolve}; };

(async()=>{
    fs.mkdirSync(out,{recursive:true});
    const browser=await chromium.launch({channel:'msedge'});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage(), cases=[], errors=[], external=[], posts=[];
    page.setDefaultTimeout(18000);
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.method()==='POST' && r.url().includes('/conversations/'))posts.push(r.url());});
    await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
    const state=async()=> (await page.request.get(base+'/__entry__/state')).json();
    const s=await state();
    const root='/api/projects/'+s.pid;
    const chat=(pid,cid)=>base+`/?page=dashboard&project=${pid}&conversation=${cid}`;
    const panel=page.locator('.ch-material-panel');
    const candidates=page.locator('.ch-messages [data-question-suggestion]');
    const answers=page.locator('.ch-message-assistant');
    const btn=(name,scope=page)=>scope.getByRole('button',{name,exact:typeof name==='string'});
    const ready=()=>page.locator('[data-ref=send]:enabled').waitFor();
    const work=()=>page.evaluate(async root=>(await (await fetch(root+'/workspace?include_chat=false')).json()).workspace,root);
    const ask=async text=>{
        const count=await answers.count();
        await page.locator('#ch-input').fill(text);
        await btn('发送').click();
        await answers.nth(count).waitFor();
        await ready();
        return answers.nth(count);
    };
    async function selectPapers(changes) {
        await btn('查看研究进展').click();
        await panel.getByRole('tab',{name:'文献与报告'}).click();
        for(const [id,checked] of changes){
            const paper=panel.locator(`[data-paper-id="${id}"]`);
            if(!await paper.evaluate(el=>el.open))await paper.locator('summary').click();
            await paper.getByRole('checkbox').setChecked(checked);
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
        const request='请整理研究现状与研究缺口，并给出一个候选研究问题。';
        await selectPapers([['title-only',true]]);
        await ask(request);
        const hypo=candidates.nth(0);
        await hypo.getByText('尚待验证的研究设想',{exact:true}).waitFor();
        assert.match(await hypo.innerText(),/仅提供题名和元信息/);
        await hypo.locator('.ch-gap-detail>summary').click();
        assert.equal(await hypo.locator('.ch-gap-source').count(),0);
        assert.match(await hypo.innerText(),/需要的资料或条件.*带图表教材/);
        assert.equal((await state()).calls.at(-1).context.papers[0].content_available,'metadata');
        assert.equal((await work()).items.length,0);
        cases.push('title-only context yields a provisional candidate and no content evidence');

        await selectPapers([['title-only',false],['scope-abstract',true],['scope-excerpt',true]]);
        await ask(request);
        const candidate=candidates.nth(1);
        await candidate.getByText('比较材料得到的可能不足',{exact:true}).waitFor();
        assert.match(await candidate.innerText(),/问题从这里来/);
        const details=candidate.locator('.ch-gap-detail');
        await details.locator('summary').focus();
        await page.keyboard.press('Enter');
        assert.equal(await details.locator('.ch-gap-source').count(),2);
        assert.match(await details.innerText(),/本轮提供的摘要/);
        assert.match(await details.innerText(),/本轮提供的正文片段 · 第 7 页/);
        const trace=(await state()).calls.at(-1);
        assert.deepEqual(trace.context.papers.map(p=>p.content_available),['abstract','excerpts']);
        assert.match(trace.system,/paper_inventory 只是目录/);
        await candidate.scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'comparison-desktop.png')});
        const beforeSource=posts.length;
        const source=details.getByRole('button',{name:'查看来源：只提供正文片段的样本',exact:true});
        await source.focus();
        await page.keyboard.press('Enter');
        await panel.locator('[data-paper-id=scope-excerpt][open]').waitFor();
        assert.equal(posts.length,beforeSource);
        await btn('返回对话',panel).click();
        cases.push('comparison displays exact scoped evidence from two sources and opens the matching paper without writes');

        await page.reload();
        await ready();
        await candidate.getByText('比较材料得到的可能不足',{exact:true}).waitFor();
        await page.setViewportSize({width:390,height:844});
        await candidate.scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'candidate-390px.png')});
        await details.locator('summary').focus();
        await page.keyboard.press('Enter');
        await details.locator('blockquote').last().scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(out,'comparison-390px.png')});
        const sid=await candidate.getAttribute('data-question-suggestion');
        const apply=page.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith('/suggestions/'+sid+'/apply'));
        await btn(/^确认这个研究问题/,candidate).click();
        assert((await apply).ok());
        await candidate.getByText('已确认',{exact:true}).waitFor();
        const formal=(await work()).items[0];
        assert.deepEqual(formal.paper_ids,['scope-abstract','scope-excerpt']);
        await btn(/^准备方法草案请求/,candidate).click();
        assert.equal(await page.locator('[data-ref=planQuestion]').inputValue(),formal.item_id);
        await page.keyboard.press('Escape');
        cases.push('refresh preserves evidence; explicit confirmation stores linked sources and opens the bound method setup');

        await ask('没看懂，先用简单的话解释。');
        assert.equal(await answers.last().locator('.ch-gap-evidence,.ch-action-card,[data-question-suggestion]').count(),0);
        assert((await state()).calls.at(-1).context.response_preferences.needs_reexplanation);
        cases.push('confusion gets a normal explanation with no new card or search');

        await ask(request);
        const stale=candidates.nth(2);
        await btn(/^确认这个研究问题/,stale).waitFor();
        const revision=(await work()).revision;
        const changed=await page.evaluate(async ({root,revision})=>(await fetch(root+'/workspace/brief',{
            method:'PUT',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({expected_revision:revision,brief:{resources:'更新后的条件仍待核对'}}),
        })).status,{root,revision});
        assert.equal(changed,200);
        await btn('查看研究进展').click();
        await stale.getByText(/项目材料或记录已更新/).waitFor({state:'attached'});
        assert.equal(await btn(/^确认这个研究问题/,stale).count(),0);
        await btn('返回对话',panel).click();
        await stale.locator('.ch-gap-detail>summary').click();
        assert.match(await stale.innerText(),/没有纳入带图表/);
        await stale.scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'historical-390px.png')});
        cases.push('material update keeps historical excerpts while removing stale confirmation');

        const held=defer(), release=defer(), finished=defer();
        await page.route('**/guidance-actions?*',async r=>{
            const response=await r.fetch();
            held.resolve();await release.promise;
            await r.fulfill({response}).catch(()=>{});
            finished.resolve();
        },{times:1});
        await btn('查看研究进展').click();
        await held.promise;
        await page.evaluate(r=>window.__sraApp.navigate(r),{projectId:s.target,conversationId:s.other,page:'dashboard'});
        await ready();
        release.resolve();await finished.promise;
        assert.equal(await page.locator('.ch-gap-evidence').count(),0);
        await page.goBack();
        await ready();
        assert.equal(await candidates.count(),3);
        cases.push('late read cannot restore evidence in another conversation; back restores the correct snapshots');
        const final=await state();
        assert.equal(final.state.public_calls,0);
        assert.equal(final.state.source_calls,0);
        assert.deepEqual(external,[]);
        assert.deepEqual(errors,[]);
        fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({cases,offlineCalls:final.calls.length,realExternalCalls:0},null,2));
        fs.writeFileSync(path.join(out,'request-evidence.json'),JSON.stringify(final.calls.map(c=>({
            text:c.text, policy:c.system.includes('【本轮研究缺口】'), papers:c.context.papers,
            preferences:c.context.response_preferences,
        })),null,2));
        console.log(JSON.stringify({browserScenarios:cases.length,offlineCalls:final.calls.length,realExternalCalls:0}));
    }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
