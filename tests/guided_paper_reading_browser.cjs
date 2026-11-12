const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8113';
const out = path.resolve('docs/guided-paper-reading');
const sentence = 'If x ≥ 2, the score may rise by 3 mg; it does not prove causation.';
const quote = 'The Birch algorithm uses the caption as its only input.';

async function main(){
    fs.mkdirSync(out,{recursive:true});
    const browser=await chromium.launch({channel:'msedge'});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();
    page.setDefaultTimeout(18000);
    const outside=[],errors=[],posts=[],cases=[];
    await context.route('**/*',route=>new URL(route.request().url()).origin===base
        ? route.continue() : (outside.push(route.request().url()),route.abort()));
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',r=>{if(r.method()==='POST' && r.url().endsWith('/messages'))posts.push(r.postDataJSON());});
    const state=async()=> (await (await page.request.get(base+'/__entry__/state')).json());
    const fixture=await state();
    const route=(pid,cid)=>base+`/?page=dashboard&project=${pid}&conversation=${cid}`;
    const project=`/api/projects/${fixture.pid}`;
    const api=project+'/conversations/'+fixture.cid;
    const panel=page.locator('.ch-material-panel');
    const result=page.getByRole('dialog',{name:'论文选段',exact:true});
    const guide=page.getByRole('dialog',{name:'带我读这篇',exact:true});
    const btn=(name,scope=page)=>scope.getByRole('button',{name,exact:true});
    const calls=async()=> (await state()).calls;
    const ready=()=>page.locator('[data-ref=send]:enabled').waitFor();
    const messages=async()=> (await (await page.request.get(base+api+'/messages')).json()).messages;
    const getView=async rid=> (await (await page.request.get(base+api+'/requests/'+rid)).json());
    const awaitCall=async count=>{
        for(let i=0;i<35;i++){if((await calls()).length>=count)return;await new Promise(r=>setTimeout(r,120));}
        throw new Error('offline call did not finish');
    };
    async function login(account){
        await page.locator('[name=email]').fill('backup-'+account.toLowerCase()+'@example.com');
        await page.locator('[name=password]').fill('Offline restore sample '+account+'!');
        await btn('登录').click();
        await ready();
    }
    async function showPapers(){
        if(!await panel.isVisible())await btn('查看研究进展').click();
        await panel.getByRole('tab',{name:'文献与报告'}).click();
    }
    async function openPaper(id){
        await showPapers();
        const card=panel.locator(`[data-paper-id="${id}"]`);
        await card.waitFor();
        if(!await card.evaluate(n=>n.open))await card.locator(':scope > summary').click();
        return card;
    }
    async function chooseRange(block){
        return block.evaluate((n,needle)=>{
            const text=n.firstChild,start=text.data.lastIndexOf(needle);
            const range=document.createRange();range.setStart(text,start);range.setEnd(text,start+needle.length);
            const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
            return {selected:selection.toString(),start:Array.from(text.data.slice(0,start)).length};
        },sentence);
    }
    try{
        await page.goto(base);await login('A');await page.goto(route(fixture.pid,fixture.cid));await ready();
        const input=page.locator('#ch-input');
        await input.fill('这段是主对话未发送草稿');
        await btn('导入／查看论文').click();
        const file=page.waitForEvent('filechooser');await btn('添加待上传论文').click();
        await(await file).setFiles({name:'keep.txt',mimeType:'text/plain',buffer:Buffer.from('Offline attachment kept intact')});
        await page.locator('[data-ref=filePending]').getByText(/keep.txt/).waitFor();
        const initial=await (await page.request.get(base+api)).json();
        const baseline=await(await page.request.get(base+project+'/workspace?include_chat=false')).json();
        await (await openPaper(fixture.pdf_id)).getByRole('checkbox').check();
        const birch=await openPaper('birch');
        await btn('带我读这篇',birch).click();
        await guide.getByText('Birch Caption Retrieval',{exact:true}).waitFor();
        assert.equal((await calls()).length,0);
        await page.screenshot({path:path.join(out,'companion-desktop.png')});
        const goal=btn('方法步骤',guide);await goal.focus();await page.keyboard.press('Enter');
        await page.locator('.ch-message-assistant').waitFor();await ready();
        assert.equal(await input.inputValue(),'这段是主对话未发送草稿');
        assert.match(await page.locator('[data-ref=filePending]').innerText(),/待提交资料：keep.txt/);
        const after=await (await page.request.get(base+api)).json();
        for(const key of ['entry_stage','discussion_step','discussion_goal','guidance_mode'])
            assert.equal(initial.conversation[key],after.conversation[key]);
        const call=(await calls())[0];
        assert.deepEqual(call.context.papers.map(p=>p.paper_id),['birch']);
        assert.equal(call.context.reading_guide.goal,'method');
        const assistant=(await messages()).at(-1);
        assert.deepEqual(assistant.reading_citations.map(c=>c.quote),[quote]);
        cases.push({name:'keyboard guide overrides old checked paper and preserves draft, file and settings',call,assistant});

        const card=await openPaper('guide-unicode');
        const block=card.locator('.ch-passage-original');
        await block.evaluate(()=>window.getSelection().removeAllRanges());
        await btn('处理选中的文字',card).click();
        await card.getByText(/请先选中一段原文/).waitFor();
        const long=await openPaper('guide-long');
        await btn('翻译这段',long).click();await long.getByText(/超过 1000 个字符/).waitFor();
        await page.evaluate(()=>{
            const a=document.querySelector('[data-paper-id="guide-unicode"] .ch-passage-original').firstChild;
            const b=document.querySelector('[data-paper-id="guide-long"] .ch-passage-original').firstChild;
            const range=document.createRange();range.setStart(a,0);range.setEnd(b,12);
            window.getSelection().removeAllRanges();window.getSelection().addRange(range);
        });
        await btn('处理选中的文字',card).click();
        await card.getByText(/跨越了不同来源/).waitFor();
        assert.equal((await calls()).length,1);
        cases.push({name:'empty, long and crossing ranges produce local messages and no model call'});

        const selected=await chooseRange(block);
        await btn('处理选中的文字',card).click();
        await result.getByText(sentence,{exact:true}).waitFor();
        assert.equal((await calls()).length,1);
        const translate=btn('翻译成中文',result);
        await translate.evaluate(n=>{n.click();n.click();});
        await result.getByRole('heading',{name:'中文翻译'}).waitFor();
        await result.getByText(/可能增加 3 mg/).waitFor();
        await page.screenshot({path:path.join(out,'translation-desktop.png')});
        const request=posts.at(-1);
        assert.equal(request.passage.start,selected.start);
        assert.equal(request.passage.text,selected.selected);
        assert.equal((await calls()).length,2);
        const view=await getView(request.request_id);
        assert.equal(view.passage.source.text,sentence);
        assert.match(view.passage.source.before,/第二处 β/);
        assert.equal(view.messages.length,0);
        assert.equal((await messages()).length,2);
        cases.push({name:'exact Unicode range, second identical sentence and double-click idempotency',request,view,call:(await calls()).at(-1)});
        await btn('关闭选段窗口',result).click();

        await page.reload();await ready();await showPapers();
        await btn('查看上次选段',panel).click();
        await result.getByRole('heading',{name:'中文翻译'}).waitFor();
        assert.equal((await calls()).length,2);
        await btn('关闭选段窗口',result).click();
        await btn('返回对话',panel).click();
        await page.request.post(base+'/__entry__/remove-birch');
        await page.setViewportSize({width:390,height:844});
        await page.reload();await ready();
        const evidence=page.locator('.ch-paper-reading');
        await evidence.locator(':scope > summary').click();
        const explain=btn('解释这段',evidence).first();await explain.focus();await page.keyboard.press('Enter');
        await result.getByRole('heading',{name:'通俗解释'}).waitFor();
        await result.getByText(/历史原文快照/).waitFor();
        await result.getByText(quote,{exact:true}).waitFor();
        assert.equal(await result.evaluate(n=>n.scrollWidth<=n.clientWidth),true);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.screenshot({path:path.join(out,'historical-explain-390px.png')});
        cases.push({name:'refresh reads stored translation; keyboard explains deleted paper from historical snapshot',call:(await calls()).at(-1)});
        await btn('关闭选段窗口',result).click();

        await page.route(base+api+'/messages',async r=>{
            await r.fetch();
            await r.abort();
        },{times:1});
        const live=await openPaper('guide-unicode');
        await chooseRange(live.locator('.ch-passage-original'));
        await btn('处理选中的文字',live).click();
        await btn('用通俗的话解释',result).click();
        await btn('核对原请求',result).waitFor();
        await awaitCall(4);
        await btn('核对原请求',result).click();
        await result.getByRole('heading',{name:'通俗解释'}).waitFor();
        assert.equal(posts.length,4);
        cases.push({name:'lost POST response recovers original result by GET only'});
        await btn('关闭选段窗口',result).click();

        let release,arrived;
        const wait=new Promise(r=>{release=r;}),entered=new Promise(r=>{arrived=r;});
        const lost=posts.at(-1).request_id;
        await page.route(base+api+'/requests/'+lost,async r=>{
            const res=await r.fetch();arrived();await wait;await r.fulfill({response:res}).catch(()=>{});
        },{times:1});
        await btn('查看上次选段',panel).click();
        await entered;
        await page.evaluate(r=>window.__sraApp.navigate(r),{projectId:fixture.target,conversationId:fixture.other,page:'dashboard'});
        release();await ready();
        assert.equal(await result.count(),0);
        const logout=btn('退出');
        if(!await logout.isVisible())await page.locator('#ch-menu').click();
        await logout.click();await login('B');
        await page.goto(route(fixture.foreign,fixture.foreign_cid));await ready();
        assert.equal(await result.count(),0);
        assert.equal((await page.request.get(base+api+'/requests/'+lost)).status(),404);
        cases.push({name:'late result is discarded on navigation and denied to another account'});
        const final=await state();
        assert.equal(final.calls.length,4);
        assert.equal(final.state.source_calls,0);assert.equal(final.state.public_calls,0);
        assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
        assert.deepEqual(baseline.workspace.items,[]);
        fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({cases,offlineCalls:4,externalCalls:0},null,2));
        console.log(JSON.stringify({browserScenarios:cases.length,offlineCalls:4,externalCalls:0}));
    }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
