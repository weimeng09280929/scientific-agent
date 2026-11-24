/* Real interface journey, temporary databases, no model/search/external access. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.SRA_TEACHING_URL || 'http://127.0.0.1:8032';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || path.join(__dirname,'..','docs','research-teaching-cases','rework'));
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true});
  const page=await context.newPage(), errors=[],outgoing=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await context.route('**/*',r=>new URL(r.request().url()).origin===base ? r.continue() : (outgoing.push(r.request().url()),r.abort()));
  const audit=async()=>(await page.request.get(base+'/__fixture__/audit')).json();
  const api=()=>{const u=new URL(page.url());return `${base}/api/projects/${u.searchParams.get('project')}/conversations/${u.searchParams.get('conversation')}`;};
  const state=async()=>(await page.request.get(api()+'/teaching')).json();
  const shared=async()=>(await page.request.get(api().split('/conversations/')[0]+'/workspace?include_chat=false')).json();
  const task=()=>page.locator('.case-task');
  async function ready(){await task().waitFor();await page.waitForFunction(()=>document.querySelector('.ch-teaching')?.dataset.busy==='false' && !document.querySelector('[data-ref="send"]')?.disabled);}
  async function choose(id){await ready();const old=(await state()).state_version;await page.locator(`[data-case-option="${id}"]`).last().click();await page.waitForFunction(old=>Number(document.querySelector('.ch-teaching')?.dataset.stateVersion)>old,old);await ready();}
  async function scrollLatest(){const b=page.locator('[data-ref="latest"]');if(await b.isVisible())await b.click();await page.locator('[data-ref="messages"]').evaluate(el=>el.scrollTop=el.scrollHeight);}
  async function shot(name){if(!await page.locator('[data-ref="panel"]').isVisible())await scrollLatest();await page.screenshot({path:path.join(shots,name)});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  async function faq(text,match){const input=page.locator('[data-ref="input"]');await input.fill(text);await input.press('Control+Enter');await page.locator('.ch-message-assistant').last().filter({hasText:match}).waitFor();await ready();}
  async function openPlan(){await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();await page.locator('.ch-material-panel .ch-plan-review[open]').last().waitFor();}
  try{
    fs.mkdirSync(shots,{recursive:true});await page.goto(base+'/workspace');
    const count=(await audit()).projects;
    await page.locator('[data-teaching="directory"]').click();await page.locator('.case-card').first().waitFor();
    assert.equal((await audit()).projects,count);await page.getByRole('button',{name:'返回对话',exact:true}).click();
    const routes=[];
    for(const [title,q,large,small,method] of [['联邦学习','compare','repeat','small','computational'],['硫化物','bulk','broader','narrow','experimental']]){
      await page.goto(base+'/workspace');await page.locator('[data-teaching="directory"]').click();
      await page.locator('.case-card').filter({hasText:title}).getByRole('button',{name:'开始体验',exact:true}).dblclick();await ready();
      const initial=(await (await page.request.get(api()+'/messages')).json()).messages;
      assert.equal(initial.length,1);assert.match(initial[0].content,/假设/);
      assert.equal((await state()).stage_title,'明确条件');
      await shot('opening-'+method+'-desktop.png');
      const draft='保留我的未发送问题';await page.locator('[data-ref="input"]').fill(draft);
      await choose('begin');assert.equal(await page.locator('[data-ref="input"]').inputValue(),draft);
      await page.locator('[data-ref="input"]').fill('');
      assert.equal((await state()).stage_title,'了解研究现状');
      await page.getByRole('button',{name:'查看这三篇文献',exact:true}).click();
      const paper=page.locator('.ch-paper-card[open]').first();await paper.waitFor();
      assert.match(await paper.innerText(),/阅读范围/);assert.match(await paper.innerText(),/与当前讨论的关系/);
      assert.equal((await shared()).workspace.items.length,0);await shot('literature-'+method+'-desktop.png');
      await page.locator('[data-ref="panelClose"]').click();await faq('这些论文能说明什么？',/案例讲解/);
      await choose('continue');assert.equal((await state()).stage_title,'确认研究问题');
      assert.equal(await page.locator('.case-candidate').count(),2);await choose(q);
      assert.equal((await shared()).workspace.items.length,0);
      await page.reload();await ready();await choose('adopt_question');
      assert.equal((await shared()).workspace.items.filter(i=>i.kind==='question').length,1);
      await choose(large);await choose('show_plan');const original=(await state()).plan_suggestion;
      const last=page.locator('.ch-message-assistant').last();for(const label of ['要研究什么','准备怎么研究','如何判断','需要的条件','待确认'])assert.match(await last.innerText(),new RegExp(label));
      assert.equal(await last.locator('.ch-plan-review').count(),0);assert.doesNotMatch(await last.innerText(),/\b[a-f0-9]{32}\b/);
      await openPlan();await shot('review-'+method+'-desktop.png');
      await page.locator('.ch-material-panel .ch-plan-review[open]').last().getByRole('button',{name:'修改条件',exact:true}).click();
      await page.locator(`[data-case-option="${small}"]`).waitFor();await choose(small);
      assert.match(await page.locator('.ch-message-assistant').last().innerText(),/本次修改/);
      await choose('show_plan');
      const suggestions=(await (await page.request.get(api()+'/suggestions')).json()).suggestions;
      assert(suggestions.find(s=>s.suggestion_id===original).basis_changed);
      await openPlan();const plan=page.locator('.ch-material-panel .ch-plan-review[open]').last();
      await plan.getByRole('button',{name:'保存当前方案',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('.ch-teaching strong')?.textContent.includes('体验已完成'));
      await page.locator('[data-ref="panelClose"]').click();await ready();
      const result=await shared();assert.equal(result.workspace.items.filter(i=>i.kind==='plan').length,1);assert(result.workflow.flow.planReady);
      assert(!result.workspace.items.some(i=>['result','finding','outline'].includes(i.kind)));
      await page.locator('[data-ref="downloadToggle"]').click();
      const entry=page.locator('[data-export-key="teaching-sources"]');await entry.waitFor();
      const pending=page.waitForEvent('download');await entry.getByRole('link',{name:'下载',exact:true}).click();
      const download=await pending,text=fs.readFileSync(await download.path(),'utf8');
      for(const marker of ['1.1.0','教学设定','未验证','来源'])assert(text.includes(marker));
      await page.getByRole('button',{name:'收起下载',exact:true}).click();
      await page.setViewportSize({width:390,height:844});await shot('completed-'+method+'-mobile.png');
      const sizes=await page.locator('.ch-messages').boundingBox();assert(sizes.height>300,'Mobile leaves at least 300px for reading');
      await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
      await page.locator('[data-material-kind="plan"] .ch-plan-review[open]').waitFor();
      assert.match(await page.locator('[data-material-kind="plan"] .ch-plan-review[open]').innerText(),/已保存方案/);
      await shot('review-'+method+'-mobile.png');await page.locator('[data-ref="panelClose"]').click();
      await faq('这个方案需要什么条件？',/案例讲解/);
      await faq('什么是量子纠缠？',/未覆盖这个自由问题/);
      assert.equal((await state()).completed,true);
      routes.push({case:title,url:page.url(),modifiedBeforeAdoption:true,download:download.suggestedFilename(),readingHeight:sizes.height});
      await page.setViewportSize({width:1440,height:900});
    }
    // Old-backend detection is explicit capability data, not a generic 404 fallback.
    const countBefore=(await audit()).projects;
    await page.route('**/api/conversation-capabilities',async route=>{const r=await route.fetch(),body=await r.json();delete body.teaching_experience;await route.fulfill({response:r,json:body});});
    await page.goto(base+'/workspace');await page.locator('[data-teaching="directory"]').click();
    await page.getByText(/当前后端尚未加载四阶段教学体验/).waitFor();assert.equal((await audit()).projects,countBefore);
    await page.unroute('**/api/conversation-capabilities');
    assert.deepEqual((await audit()).external_calls,[]);assert.deepEqual(errors,[]);assert.deepEqual(outgoing,[]);
    console.log(JSON.stringify({ok:true,routes,externalCalls:[],consoleErrors:[],screenshots:shots}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
