/* Restored real FastAPI + temporary accounts, real UI downloads, offline edges. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base=process.env.SRA_BACKUP_URL||'http://127.0.0.1:8094',out=path.resolve(process.env.SRA_BACKUP_SHOTS||'docs/local-backup-restore/browser');
const emails=['backup-a@example.com','backup-b@example.com'];
const ready=p=>p.locator('[data-ref="send"]:not(:disabled)').waitFor();
const route=(p,cid=p.conversation_id,page='dashboard')=>`${base}/?page=${page}&project=${p.project_id}&conversation=${cid}`;
async function login(p,i){await p.goto(base);await p.locator('[name=email]').fill(emails[i]);await p.locator('[name=password]').fill(`Offline restore sample ${i?'B':'A'}!`);await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
async function json(p,url,method='GET',body){return p.evaluate(async({url,method,body})=>{const r=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});return {status:r.status,data:await r.json()};},{url,method,body});}
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),contexts=[],errors=[],outside=[],downloads=[];
 let a,b;
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900},acceptDownloads:true});contexts.push(context);
   await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(r.request().url()),r.abort()));
  }
  a=await contexts[0].newPage();b=await contexts[1].newPage();for(const p of [a,b])p.on('pageerror',e=>errors.push(e.message));
  const initial=await(await a.request.get(base+'/__backup__/state')).json(),sample=initial.sample;
  assert.equal(initial.guidance_calls+initial.model_calls+initial.source_calls,0);
  assert.equal((await a.request.get(base+'/api/projects')).status(),401);
  await login(a,0);await login(b,1);const records=sample.users;
  for(let i=0;i<2;i++){
   const p=[a,b][i],info=records[i],other=records[1-i],api=`/api/projects/${info.project_id}`;
   await p.goto(route(info));await ready(p);
   const own=await json(p,'/api/projects');assert.deepEqual(own.data.projects.map(x=>x.project_id),[info.project_id]);
   assert.equal((await json(p,`/api/projects/${other.project_id}/workspace`)).status,404);
   assert.equal((await json(p,`/api/projects/${other.project_id}/notes`,'POST',{text:'must not write'})).status,404);
   for(const item of Object.values(other.downloads))assert.equal((await p.request.get(base+item.url)).status(),404);
   assert.equal((await json(p,api+`/conversations/${other.conversation_id}`)).status,404);
   assert.equal((await json(p,'/api/conversations/start','POST',info.start_body)).data.project_id,info.project_id);
   const w=(await json(p,api+'/workspace')).data.workspace,plans=w.items.filter(x=>x.kind==='plan');
   assert.equal(plans.length,2);assert.equal(plans.find(x=>x.item_id===info.plan_ids[1]).status,'active');
   // Download all preserved plan/report/artifact versions from the actual menu.
   await p.locator('[data-ref="downloadToggle"]').click();
   for(const [name,item] of Object.entries(info.downloads).filter(([n])=>n!=='attachment.pdf')){
    const selector=`a[download][href^="${item.url.replace('?format=md','')}"]`;
    const link=p.locator('[data-ref="downloadBody"]').locator(selector).first();await link.waitFor({state:'attached'});
    const group=link.locator('xpath=ancestor::details[contains(@class,"ch-export-group")]');
    if(await group.count())await group.locator('summary').first().click();
    const event=p.waitForEvent('download');await link.click();const file=await event;
    const saved=path.join(out,`account-${i?'b':'a'}-${name}`);await file.saveAs(saved);
    assert.equal(sha(saved),item.sha256,name);downloads.push({account:i?'b':'a',file:path.basename(saved),sha256:item.sha256,bytes:fs.statSync(saved).size});
    if(await group.count())await group.locator('summary').first().click();
   }
   await p.screenshot({path:path.join(out,`account-${i?'b':'a'}-versions-${i?'390px':'desktop'}.png`)});
   await p.locator('[data-ref="downloadToggle"]').click();
   await p.goto(route(info,info.conversation_id,'papers'));
   const pdf=p.locator(`.list-item[data-paper-id="${info.paper_id}"] .btn-pdf-download`);await pdf.waitFor();
   const event=p.waitForEvent('download');await pdf.click();const file=await event,saved=path.join(out,`account-${i?'b':'a'}-attachment.pdf`);await file.saveAs(saved);
   assert.equal(sha(saved),info.downloads['attachment.pdf'].sha256);downloads.push({account:i?'b':'a',file:path.basename(saved),sha256:sha(saved),bytes:fs.statSync(saved).size});
   await p.goto(route(info,info.second_conversation_id));await ready(p);
   const interrupted=await json(p,api+`/conversations/${info.second_conversation_id}/requests/interrupted-chat`);
   assert.equal(interrupted.data.status,'interrupted');assert.equal(interrupted.data.attempt,1);assert.equal(interrupted.data.messages.length,1);
   const structure=p.locator('[data-request-id="failed-structure"]');assert.equal(await structure.getByRole('button',{name:'重试此消息',exact:true}).count(),0);
   const task=await json(p,api+`/conversations/${info.task_conversation_id}/research-tasks/${info.interrupted_task_id}`);assert.equal(task.data.status,'interrupted');
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await p.screenshot({path:path.join(out,`interrupted-${i?'390px':'desktop'}.png`)});
  }
  const quiet=await(await a.request.get(base+'/__backup__/state')).json();assert.equal(quiet.guidance_calls+quiet.model_calls+quiet.source_calls,0);
  // Only this explicit retry may execute. The existing formal v2 remains active.
  const info=records[0],api=`/api/projects/${info.project_id}`;
  const retry=a.locator('[data-request-id="interrupted-chat"]').getByRole('button',{name:'重试此消息',exact:true});await retry.click();
  let recovered;const deadline=Date.now()+20000;
  for(;;){
   recovered=(await json(a,api+`/conversations/${info.second_conversation_id}/requests/interrupted-chat`)).data;
   if(recovered.status==='completed'&&recovered.attempt===2&&recovered.messages.length===2&&recovered.suggestions.length)break;
   assert(Date.now()<deadline,'Original request and persisted reply/draft did not become readable');await a.waitForTimeout(50);
  }
  assert.deepEqual(recovered.messages.map(x=>x.role),['user','assistant']);assert(recovered.suggestions.length&&!recovered.suggestions[0].applied_item_id);
  assert.equal((await json(a,api+'/workspace')).data.workspace.items.filter(x=>x.kind==='plan').length,2);
  // A normal saved note and account switch operate only in the restored copy.
  await b.goto(route(records[1],records[1].conversation_id,'notes'));await b.locator('#note-input').fill('仅恢复副本的新笔记');await b.locator('#note-input').press('Control+Enter');
  await b.waitForFunction(async url=>(await(await fetch(url)).json()).notes.some(x=>x.text==='仅恢复副本的新笔记'),`/api/projects/${records[1].project_id}/notes`);
  await a.locator('#ch-input').fill('A 的未提交草稿不属于服务器备份，也不能带给 B');
  const tab=await contexts[0].newPage();await tab.goto(route(info));await ready(tab);
  await a.locator('#account-logout').click();await a.locator('[name=email]').waitFor();await tab.locator('[name=email]').waitFor();
  await login(a,1);assert.equal(await a.locator('#ch-input').inputValue(),'');
  await a.goto(route(records[1]));await ready(a);assert.equal(await a.locator('#ch-input').inputValue(),'');
  await a.setViewportSize({width:390,height:900});await a.screenshot({path:path.join(out,'switched-account-390px.png')});
  assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const final=await(await a.request.get(base+'/__backup__/state')).json();assert.equal(final.guidance_calls,1);assert.equal(final.public_calls,0);assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  const result={passed:true,twoIndependentAccounts:true,actualDownloads:downloads,oldRequestsNotReplayed:true,readModelExecutions:0,explicitRetryExecutions:1,noDuplicateMessages:true,formalV2Preserved:true,multiTabLogoutAndAccountSwitch:true,desktopAnd390px:true,realExternalCalls:0};
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(result,null,2));console.log(`PASS restored two-account browser journey; ${downloads.length} actual downloads verified`);
 }catch(e){if(a)await a.screenshot({path:path.join(out,'failure.png')});fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({passed:false,error:String(e.message).replace(/Offline restore sample [AB]!/g,'[redacted]')},null,2));throw e;}
 finally{for(const c of contexts)await c.close();await browser.close();}
})().catch(e=>{console.error(String(e.stack||e).replace(/Offline restore sample [AB]!/g,'[redacted]'));process.exitCode=1;});
