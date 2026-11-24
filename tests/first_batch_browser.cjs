/* Actual primary app + temporary FastAPI. Only network delays/failures injected. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_FIRST_URL||'http://127.0.0.1:8063',out=path.resolve(process.env.SRA_FIRST_SHOTS || 'docs/first-batch-reliability-fixes');
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));const api=page.request;
 const start=async(label)=>(await(await api.post(base+'/api/conversations/start',{data:{request_id:'notes-'+label,title:'草稿保护 '+label,domain:'ai',entry_stage:'orientation'}})).json());
 const A=await start('A'),B=await start('B');let posts=0;
 const open=async(p,pageName='notes')=>{await page.goto(`${base}/workspace?page=${pageName}&project=${p.project_id}&conversation=${p.conversation_id}`);await page.locator(pageName==='notes'?'#content #note-input':'[data-ref="input"]').waitFor();};
 const input=()=>page.locator('#content #note-input');
 const shot=async name=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,name+'.png')});};
 const notes=async p=>(await(await api.get(`${base}/api/projects/${p.project_id}/notes`)).json()).notes;
 try {
  await open(A);await input().fill('A 未提交草稿');await page.reload();await input().waitFor();assert.equal(await input().inputValue(),'A 未提交草稿');
  await page.locator('#nav [data-page="papers"]').click();await page.locator('#nav [data-page="notes"]').click();await input().waitFor();assert.equal(await input().inputValue(),'A 未提交草稿');
  await open(B);assert.equal(await input().inputValue(),'');await input().fill('B 独立草稿');await open(A);assert.equal(await input().inputValue(),'A 未提交草稿');
  let release,started;const gate=new Promise(r=>release=r),entered=new Promise(r=>started=r);
  const pattern='**/api/projects/*/notes';
  await page.route(pattern,async route=>{if(route.request().method()!=='POST')return route.continue();posts++;const response=await route.fetch();started();await gate;await route.fulfill({response});});
  await input().press('Control+Enter');await entered;
  // Repeat key and button while saving cannot create another request.
  await input().dispatchEvent('keydown',{key:'Enter',ctrlKey:true,repeat:true});
  await open(B);assert.equal(await input().inputValue(),'B 独立草稿');release();
  await page.waitForFunction(()=>!document.querySelector('#content .btn-row .btn')?.disabled);
  assert.equal(await input().inputValue(),'B 独立草稿');assert.equal(posts,1);assert.equal((await notes(A)).length,1);assert.equal((await notes(B)).length,0);
  await shot('notes-cross-project-desktop');await page.unroute(pattern);
  // Saving one snapshot must not consume text typed after submission.
  await open(A);await input().fill('本次提交快照');
  let release2,entered2;const gate2=new Promise(r=>release2=r),started2=new Promise(r=>entered2=r);
  await page.route(pattern,async route=>{if(route.request().method()!=='POST')return route.continue();posts++;const response=await route.fetch();entered2();await gate2;await route.fulfill({response});});
  await input().press('Meta+Enter');await started2;await input().fill('保存期间的新想法');release2();
  await page.getByText('本次笔记已保存；新输入仍是未提交草稿。',{exact:true}).waitFor();assert.equal(await input().inputValue(),'保存期间的新想法');assert.equal((await notes(A)).length,2);assert.equal(posts,2);
  await page.reload();await input().waitFor();assert.equal(await input().inputValue(),'保存期间的新想法');await page.unroute(pattern);
  await page.route(pattern,route=>route.request().method()==='POST'?route.abort('failed'):route.continue());
  await input().press('Control+Enter');await page.getByText('保存未确认，草稿仍保留。可先查看历史笔记，再点击保存重试。',{exact:true}).waitFor();assert.equal(await input().inputValue(),'保存期间的新想法');
  await page.unroute(pattern);await page.setViewportSize({width:390,height:844});await shot('notes-draft-mobile');
  await page.getByRole('button',{name:'清空',exact:true}).click();await page.reload();await input().waitFor();assert.equal(await input().inputValue(),'');
  // Create failure must not leave an optimistic creating/waiting message.
  await page.setViewportSize({width:1440,height:900});await open(B,'dashboard');await page.locator('#ch-project-new').click();await page.locator('[data-ref="input"]').fill('保留新对话输入');
  await page.route('**/api/conversations/start',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'隔离故障：会话创建失败'})}));
  await page.locator('[data-ref="send"]').click();await page.getByText('隔离故障：会话创建失败',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-ref="input"]').inputValue(),'保留新对话输入');assert.doesNotMatch(await page.locator('.ch-messages').innerText(),/正在建立|仍在处理中/);
  await page.setViewportSize({width:390,height:844});await shot('create-failure-mobile');await page.unroute('**/api/conversations/start');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,posts,errors,projects:[A.project_id,B.project_id]},null,2));console.log('PASS: shortcut, draft recovery/isolation, delayed responses, failure/clear, create rollback, desktop/mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
