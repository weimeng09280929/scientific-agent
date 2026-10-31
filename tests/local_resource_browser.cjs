/* Real isolated FastAPI, two accounts/tabs; barriers control actual work. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8088',out=path.resolve(process.env.SRA_RESOURCE_SHOTS||'docs/local-resource-protection');
async function login(p,suffix){
 await p.goto(base);await p.locator('[name=email]').fill(`tester-${suffix.toLowerCase()}@example.com`);
 await p.locator('[name=password]').fill('browser acceptance '+suffix);
 await p.getByRole('button',{name:'登录',exact:true}).click();
 await p.locator('[data-ref="send"]:not(:disabled)').waitFor();
}
async function api(p,url,body){return p.evaluate(async({url,body})=>{
 const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 return {status:r.status,data:await r.json()};
},{url,body});}
async function create(p,id){const r=await api(p,'/api/conversations/start',{request_id:id,title:'离线资源保护验收',domain:'ai'});assert.equal(r.status,200);return r.data;}
const messages=r=>`/api/projects/${r.project_id}/conversations/${r.conversation_id}/messages`;
const route=r=>`${base}/?page=dashboard&project=${r.project_id}&conversation=${r.conversation_id}`;
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const ca=await browser.newContext({viewport:{width:1440,height:960}}),cb=await browser.newContext({viewport:{width:390,height:844}});
 const a=await ca.newPage(),b=await cb.newPage(),errors=[],outside=[];
 for(const c of [ca,cb])await c.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(1),r.abort()));
 for(const p of [a,b])p.on('pageerror',e=>errors.push(e.message));
 try{
  await login(a,'A');await login(b,'B');
  const p1=await create(a,'browser-a-one'),p2=await create(a,'browser-a-two'),pb=await create(b,'browser-b');
  // Build an actual saved offline report before saturating the single test slot.
  const tasks=messages(pb).replace('/messages','/research-tasks');
  const task=await api(b,tasks,{request_id:'b-report',content:'检索离线资料','topic':'offline account research'});
  assert.equal(task.status,200);
  await b.waitForFunction(async({url})=>{const r=await(await fetch(url)).json();return r.status==='completed'&&!r.work_still_running;},
   {url:tasks+'/'+task.data.task_id},{timeout:20000});
  for(let i=0;;i++){
   const state=await(await b.request.get(base+'/__resource__/state')).json();
   if(state.heavy===0)break;
   assert(i<100,'Previous report worker has not exited');await b.waitForTimeout(50);
  }
  const exportURL=`/api/projects/${pb.project_id}/research-tasks/${task.data.task_id}/export?version=1`;
  await a.request.post(base+'/__resource__/hold');
  await a.evaluate(url=>{window.resourcePending=fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:'blocking-a',content:'解释研究范围'})}).then(async r=>({status:r.status,data:await r.json()}));},messages(p1));
  if(!(await(await a.request.get(base+'/__resource__/entered')).json()).entered){
   const failed=await a.evaluate(()=>window.resourcePending);
   throw new Error('Controlled guidance did not begin: '+JSON.stringify(failed));
  }
  const tab=await ca.newPage();tab.on('pageerror',e=>errors.push(e.message));
  await tab.goto(route(p2));await tab.locator('[data-ref="send"]:not(:disabled)').waitFor();
  const posts=[];tab.on('request',r=>{if(r.url().endsWith('/messages')&&r.method()==='POST')posts.push(JSON.parse(r.postData()).request_id);});
  await tab.locator('#ch-input').fill('容量拒绝后必须保留的原文字');
  const rejected=tab.waitForResponse(r=>r.url().endsWith('/messages')&&r.status()===429);
  await tab.locator('[data-ref="send"]').click();await rejected;
  await tab.getByText('本账号已有操作占用处理名额',{exact:false}).first().waitFor();
  assert.equal(await tab.locator('#ch-input').inputValue(),'容量拒绝后必须保留的原文字');
  await tab.screenshot({path:path.join(out,'account-busy-desktop.png')});
  // Other user reads and actually downloads the saved report while A is blocked.
  const download=await b.request.get(base+exportURL);assert.equal(download.status(),200);
  const report=await download.text();assert(report.includes('参考文献与阅读范围'));
  fs.writeFileSync(path.join(out,'b-owned-report-v1.md'),report);
  assert.equal((await api(b,'/api/projects')).status,200);
  assert.equal((await api(b,`/api/projects/${p1.project_id}/status`)).status,404);
  await b.goto(route(pb));await b.locator('[data-ref="send"]:not(:disabled)').waitFor();
  await b.locator('#ch-input').fill('系统繁忙也应保留 B 的文字');
  const globalRejected=b.waitForResponse(r=>r.url().endsWith('/messages')&&r.status()===429);
  await b.locator('[data-ref="send"]').click();await globalRejected;
  await b.getByText('系统当前繁忙',{exact:false}).first().waitFor();
  assert.equal(await b.locator('#ch-input').inputValue(),'系统繁忙也应保留 B 的文字');
  assert.equal(await b.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await b.screenshot({path:path.join(out,'system-busy-narrow.png')});
  const during=await(await a.request.get(base+'/__resource__/state')).json();
  assert.equal(during.guidance_calls,1);assert.equal(during.heavy,1);assert.equal(during.peak_guidance,1);
  await a.request.post(base+'/__resource__/release');
  assert.equal((await a.evaluate(()=>window.resourcePending)).status,200);
  const resumed=tab.waitForResponse(r=>r.url().endsWith('/messages')&&r.status()===200);
  await tab.locator('[data-ref="send"]').click();await resumed;
  assert.equal(posts.length,2);assert.equal(posts[0],posts[1]);
  const saved=await api(tab,messages(p2)+'?tail=true&limit=100');
  assert.deepEqual(saved.data.messages.map(m=>m.role),['user','assistant']);
  // UI refuses an oversized file before hashing or sending; text stays intact.
  await tab.locator('#ch-input').fill('超限附件不应清除这段文字');
  await tab.locator('[data-ref="import"]').click();
  const chooser=tab.waitForEvent('filechooser');await tab.getByRole('button',{name:'添加待上传论文',exact:true}).click();
  const fc=await chooser;await fc.setFiles({name:'too-large.txt',mimeType:'text/plain',buffer:Buffer.alloc(513,120)});
  await tab.getByText('文件超过大小限制',{exact:false}).first().waitFor();
  assert.equal(await tab.locator('#ch-input').inputValue(),'超限附件不应清除这段文字');
  await tab.screenshot({path:path.join(out,'file-too-large-desktop.png')});
  await tab.setViewportSize({width:390,height:844});
  assert.equal(await tab.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await tab.screenshot({path:path.join(out,'file-too-large-narrow.png')});
  // Refresh retains original-account text and rejected request identity.
  await b.reload();await b.locator('#ch-input').waitFor();
  assert.equal(await b.locator('#ch-input').inputValue(),'系统繁忙也应保留 B 的文字');
  await a.request.post(base+'/__resource__/hold-source');
  const taskURL=messages(p1).replace('/messages','/research-tasks');
  const cancellable=await api(a,taskURL,{request_id:'cancel-pending',content:'检索离线资料',topic:'offline cancellation'});
  assert.equal(cancellable.status,200);
  assert((await(await a.request.get(base+'/__resource__/source-entered')).json()).entered);
  const taskId=cancellable.data.task_id;
  const cancel=await api(a,taskURL+'/'+taskId+'/cancel',{});
  assert.equal(cancel.data.work_still_running,true);assert(cancel.data.cancel_note.includes('尚未结束'));
  await a.setViewportSize({width:390,height:844});await a.goto(route(p1));
  await a.getByText('取消已提出，当前操作尚未结束',{exact:false}).first().waitFor();
  await a.screenshot({path:path.join(out,'cancel-pending-narrow.png')});
  assert.equal((await api(a,taskURL+'/'+taskId+'/retry',{})).data.attempts,1);
  assert.equal((await(await a.request.get(base+'/__resource__/state')).json()).heavy,1);
  await a.request.post(base+'/__resource__/release-source');
  await a.waitForFunction(async url=>{const r=await(await fetch(url)).json();return r.status==='cancelled'&&!r.work_still_running;},taskURL+'/'+taskId);
  const end=await(await a.request.get(base+'/__resource__/state')).json();
  assert.equal(end.peak_guidance,1);assert.equal(end.heavy,0);assert.equal(end.receive,0);
  assert.equal(end.public_calls,0);assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  const result={passed:true,twoIndependentAccounts:true,sameAccountTwoTabs:true,accountAndSystemBusyVisible:true,
   originalRequestIdReused:true,exactlyOneUserAndAssistantMessage:true,inputRetainedAfterRejectionAndRefresh:true,
   oversizedFileNotSent:true,otherUserOwnedReportDownloaded:true,cancelPendingStillOccupiesSlot:true,desktopAnd390px:true,
   actualPeakGuidance:end.peak_guidance,activeAfter:0,realModelCalls:0,publicSourceCalls:0,publicCalls:end.public_calls};
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await a.request.post(base+'/__resource__/release').catch(()=>{});await a.request.post(base+'/__resource__/release-source').catch(()=>{});await browser.close();}
})().catch(e=>{console.error(String(e.stack||e).replace(/browser acceptance [AB]/g,'[redacted]'));process.exitCode=1;});
