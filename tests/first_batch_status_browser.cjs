/* Dropped delivery after server acceptance; actual persisted request + polling. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const base='http://127.0.0.1:8063',shots=path.resolve('docs/first-batch-reliability-fixes');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:900}}),api=page.request,errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  const started=await(await api.post(base+'/api/conversations/start',{data:{request_id:'status-first',title:'指导状态恢复',domain:'ai'}})).json();
  const {project_id:pid,conversation_id:cid}=started;
  await api.post(base+'/__reliability__/mode',{data:{guide_mode:'slow'}});
  await page.goto(`${base}/workspace?project=${pid}&conversation=${cid}&page=dashboard`);
  await page.waitForFunction(()=>document.querySelector('[data-ref="send"]')&&!document.querySelector('[data-ref="send"]').disabled);
  let accepted;const sent=new Promise(r=>accepted=r);let downstream;
  await page.route('**/conversations/*/messages',async route=>{
   if(route.request().method()!=='POST')return route.continue();
   downstream=route.fetch({timeout:60000});downstream.catch(()=>{});
   for(let i=0;i<60;i++){const s=await(await api.get(base+'/__reliability__/state')).json();if(s.model_calls.some(c=>c.kind==='guidance_waiting'))break;await new Promise(r=>setTimeout(r,100));}
   accepted();await route.abort('failed');
  });
  await page.locator('[data-ref="input"]').fill('请解释联邦学习概念，不生成方案。');await page.locator('[data-ref="send"]').click();await sent;
  await page.getByText('响应暂未返回，原请求仍在处理中；可切换会话，不会自动重发。',{exact:true}).waitFor();
  assert.match(await page.locator('.ch-request-status').innerText(),/正在等待科研指导回复/);assert.doesNotMatch(await page.locator('.ch-request-status').innerText(),/等待方案/);
  await page.screenshot({path:path.join(shots,'guidance-waiting-desktop.png')});
  await api.post(base+'/__reliability__/release');await downstream;
  await page.locator('.ch-message-assistant').waitFor();await page.waitForFunction(()=>!document.querySelector('.ch-request-status'));
  assert.doesNotMatch(await page.locator('[data-ref="error"]').innerText(),/仍在处理中/);
  const rows=await(await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages`)).json();assert.equal(rows.messages.length,2);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(shots,'guidance-delivered-mobile.png')});await page.reload();await page.locator('.ch-message-assistant').waitFor();assert.equal(await page.locator('.ch-message-user').count(),1);assert.deepEqual(errors,[]);
  console.log('PASS: ordinary guidance phase, lost delivery, persistent polling, terminal clears wait, refresh no duplicate');
 }finally{await api.post(base+'/__reliability__/release').catch(()=>{});await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
