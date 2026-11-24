const { chromium }=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.SRA_TEACHING_URL || 'http://127.0.0.1:8022';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || path.join(__dirname,'..','docs','research-teaching-cases'));
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const ctx=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true});
  const page=await ctx.newPage(),errors=[],outgoing=[];
  page.on('pageerror',e=>errors.push(String(e)));
  // Permit ONLY the local acceptance app, including all actual JSON interfaces.
  await ctx.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){outgoing.push(route.request().url());return route.abort();}return route.continue();});
  const audit=async()=>(await page.request.get(base+'/__fixture__/audit')).json();
  const noOverflow=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const messageUrl=()=>{const u=new URL(page.url());return `${base}/api/projects/${u.searchParams.get('project')}/conversations/${u.searchParams.get('conversation')}/messages`;};
  async function lostReply(){
    const pattern='**/teaching/choices';let once=true;
    await page.route(pattern,async route=>{
      if(!once)return route.continue();once=false;
      await route.fetch();await route.abort('failed'); // server committed, client received no reply
    });
    const input=page.locator('[data-ref="input"]');await input.fill('这段草稿不能被教学选项覆盖');
    const b=page.locator('[data-case-option="explain"]');await b.focus();await page.keyboard.press('Enter');
    await page.getByRole('button',{name:'核对原选择',exact:true}).waitFor();
    assert.equal(await input.inputValue(),'这段草稿不能被教学选项覆盖');
    const before=await (await page.request.get(messageUrl())).json();
    await page.unroute(pattern);await page.reload();
    await page.getByRole('button',{name:'核对原选择',exact:true}).click();
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='核对原选择'));
    const after=await (await page.request.get(messageUrl())).json();
    assert.deepEqual(after.messages,before.messages);
    await page.locator('[data-ref="input"]').fill('');
  }
  async function lateChoice(a,b){
    const ua=new URL(a),ub=new URL(b);
    const navigate=async u=>page.evaluate(({pid,cid})=>window.__sraApp.navigate({projectId:pid,conversationId:cid,page:'dashboard'}),{pid:u.searchParams.get('project'),cid:u.searchParams.get('conversation')});
    const bapi=`${base}/api/projects/${ub.searchParams.get('project')}/conversations/${ub.searchParams.get('conversation')}`;
    const bmessages=await (await page.request.get(bapi+'/messages')).json();
    await navigate(ua);await page.locator('[data-case-option="scope_back"]').waitFor();
    let release,committed;const gate=new Promise(r=>release=r),ready=new Promise(r=>committed=r);
    const pattern='**/teaching/choices';
    await page.route(pattern,async route=>{const response=await route.fetch();committed();await gate;await route.fulfill({response});});
    await page.locator('[data-case-option="scope_back"]').click();await ready;
    await navigate(ub);await page.locator('.ch-teaching').waitFor({state:'visible'});
    await page.waitForFunction(()=>document.querySelector('.ch-teaching strong')?.textContent.includes('体验已完成'));
    const response=page.waitForResponse(r=>r.url().includes('/teaching/choices'));
    release();await response;await page.unroute(pattern);
    assert.equal(new URL(page.url()).searchParams.get('project'),ub.searchParams.get('project'));
    assert.deepEqual((await (await page.request.get(bapi+'/messages')).json()).messages,bmessages.messages);
    const aapi=`${base}/api/projects/${ua.searchParams.get('project')}/conversations/${ua.searchParams.get('conversation')}/teaching`;
    assert.equal((await (await page.request.get(aapi)).json()).step,4); // switching did not cancel the original write
    assert.match(await page.locator('.ch-teaching strong').innerText(),/体验已完成/);
  }
  async function choose(id){const b=page.locator(`[data-case-option="${id}"]`);await b.waitFor();const old=Number(await page.locator('.ch-teaching').getAttribute('data-state-version'));await b.click();await page.waitForFunction(old=>{const box=document.querySelector('.ch-teaching');return Number(box?.dataset.stateVersion)>old && box?.dataset.busy==='false';},old);}
  async function lesson(title,q,scope){
    await page.goto(base+'/workspace');await page.locator('[data-teaching="directory"]').click();
    await page.locator('.case-card').filter({hasText:title}).getByRole('button',{name:'开始体验',exact:true}).dblclick();
    await page.locator('.ch-teaching').waitFor({state:'visible'});
    if(title==='联邦学习')await lostReply();else await choose('explain');
    assert.match(await page.locator('.ch-teaching strong').innerText(),/第 1 阶段/);
    await choose('begin');await choose('continue');await choose(q);
    await page.reload();await page.locator('[data-case-option="adopt_question"]').waitFor();
    await choose('adopt_question');await choose(scope);await choose('show_plan');
    await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
    await page.locator('.ch-material-panel .ch-plan-review[open]').waitFor();
    await page.screenshot({path:path.join(shots,title==='联邦学习'?'plan-computational-desktop.png':'plan-materials-desktop.png')});
    await noOverflow();await choose('adopt_plan');await page.waitForFunction(()=>document.querySelector('.ch-teaching strong')?.textContent.includes('体验已完成'));await page.locator('[data-ref="panelClose"]').click();
    const url=page.url();await page.reload();await page.locator('.ch-teaching').waitFor({state:'visible'});assert.match(await page.locator('.ch-teaching strong').innerText(),/体验已完成/);
    await page.locator('[data-ref="downloadToggle"]').click();
    const entry=page.locator('[data-export-key="teaching-sources"]');await entry.waitFor();
    const download=page.waitForEvent('download');await entry.getByRole('link',{name:'下载',exact:true}).click();const dl=await download;
    const text=fs.readFileSync(await dl.path(),'utf8');assert.match(text,/教学案例/);assert.match(text,/1.1.0/);assert.match(text,/待讨论/);
    await page.getByRole('button',{name:'收起下载',exact:true}).click();
    await page.locator('[data-ref="input"]').fill('帮我实时检索，不要直接调用模型');await page.locator('[data-ref="send"]').click();
    await page.getByText(/标准教学路径未覆盖这个自由问题/).waitFor();
    await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]')?.disabled);
    return url;
  }
  try{
    const initialProjects=(await audit()).projects;
    await page.goto(base+'/');await page.locator('[data-teaching="directory"]').click();await page.locator('.case-card').first().waitFor();
    assert.equal((await audit()).projects,initialProjects); // Directory viewing creates nothing, even when the fixture already has projects.
    await page.screenshot({path:path.join(shots,'catalog-desktop.png')});await page.getByRole('button',{name:'返回对话',exact:true}).click();
    const fl=await lesson('联邦学习','compare','small');
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(shots,'completed-mobile.png')});await noOverflow();
    await page.getByRole('button',{name:'研究材料',exact:true}).click();await page.screenshot({path:path.join(shots,'materials-mobile.png')});await noOverflow();await page.locator('[data-ref="panelClose"]').click();
    await page.getByRole('button',{name:'带着这个思路开始自己的研究',exact:true}).click();
    await page.getByText(/将带入的参考问题（未确认）/).waitFor();
    await page.locator('.case-transfer-preview summary').click();await page.screenshot({path:path.join(shots,'fork-mobile.png')});await noOverflow();
    await page.locator('[name="title"]').fill('我的联邦学习新问题');await page.locator('[name="resources"]').fill('只有 CPU');await page.getByRole('button',{name:'确认创建普通研究',exact:true}).click();
    await page.waitForURL(url=>url.toString()!==fl && !url.toString().includes(new URL(fl).searchParams.get('project')));
    await page.setViewportSize({width:1440,height:900});const materials=await lesson('硫化物','interface','broader');
    await lateChoice(fl,materials);
    assert.deepEqual((await audit()).external_calls,[]);assert.deepEqual(errors,[]);assert.deepEqual(outgoing,[]);
    console.log(JSON.stringify({ok:true,twoCases:true,exports:true,lostReplyReplay:true,lateChoiceIsolated:true,keyboard:true,draftPreserved:true,offline:true,pageErrors:errors,externalRequests:outgoing,screenshots:shots}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
