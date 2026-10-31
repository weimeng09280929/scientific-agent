/* Real main app + temporary FastAPI/SQLite. No product-route mocks. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_DELIVERY_URL||'http://127.0.0.1:8068',out=path.resolve('docs/research-delivery-closure'),before=process.argv.includes('--before');
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await context.newPage(),errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
 const get=async u=>{const r=await page.request.get(base+u);assert.ok(r.ok(),await r.text());return r.json();};
 const post=async(u,data)=>{const r=await page.request.post(base+u,{data});assert.ok(r.ok(),await r.text());return r.json();};
 const ready=()=>page.locator('[data-ref="send"]:not(:disabled)').waitFor();
 let pid,cid,prefix;
 const download=async(task,version)=>{const r=await page.request.get(`${base}/api/projects/${pid}/research-tasks/${task}/export?version=${version}`);assert.equal(r.status(),200);return r.text();};
 const shot=async name=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,name+'.png')});};
 try{
  const fixture=await get('/__fixture__/state');pid=fixture.project_id;cid=(await post(`/api/projects/${pid}/conversations`,{title:'研究现状交付离线验收',guidance_mode:'beginner',entry_stage:'literature'})).conversation.conversation_id;prefix=`/api/projects/${pid}/conversations/${cid}`;
  await post('/__delivery__/mode',{mode:'partial'});
  await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`);await ready();
  await page.locator('[data-ref="input"]').fill('请检索联邦学习数据异质性方法比较的代表性文献，并整理研究现状。');await page.locator('[data-ref="send"]').click();
  let task;
  for(let i=0;i<100;i++){task=(await get(prefix+'/research-tasks')).tasks[0];if(task&&['partial','completed','failed'].includes(task.status))break;await page.waitForTimeout(150);}
  assert.equal(task.status,'partial');const card=page.locator(`.ch-task-card[data-task-id="${task.task_id}"]`);await card.waitFor();
  await page.locator('.ch-message-assistant').last().waitFor();
  if(!before)await page.locator('.ch-message-assistant').filter({hasText:'研究要点'}).waitFor();
  const original=before ? await(await page.request.get(`${base}/api/projects/${pid}/research-tasks/${task.task_id}/export`)).text() : await download(task.task_id,1);fs.writeFileSync(path.join(out,before?'before-report.md':'offline-report-v1.md'),original);
  await shot(before?'before-dialogue-desktop':'after-dialogue-desktop');
  await page.setViewportSize({width:390,height:844});await shot(before?'before-dialogue-narrow':'after-dialogue-narrow');
  if(before){console.log('Captured original delivery and report');return;}
  const messages=(await get(prefix+'/messages?recent=true')).messages,reply=messages.filter(m=>m.research_task_id===task.task_id).at(-1);
  assert.ok(reply);assert.match(reply.content,/研究要点/);assert.doesNotMatch(reply.content,/查询：|有效记录累计/);
  assert.ok(reply.suggested_replies.length>=2);assert.ok(reply.cited_paper_ids.length>0);
  assert.match(original,/## 参考文献与阅读范围/);assert.match(original,/Fixture Author/);assert.match(original,/https:\/\/fixture.invalid/);
  assert.doesNotMatch(original.split('## 参考文献与阅读范围')[0],/cand-/);
  const modelCalls=(await get('/__delivery__/state')).model_calls.length;
  // Open a real citation in the shared panel, then return without generating.
  await page.locator('.ch-citation').first().click();await page.locator('[data-ref="panel"]:not([hidden])').waitFor();
  await page.locator('[data-ref="panelClose"]').click();
  const row=page.locator(`.ch-message[data-message-id="${reply.message_id}"]`);
  await row.getByRole('button',{name:'打开完整报告',exact:true}).click();await page.locator('[data-ref="materialBody"]').getByText(/参考文献与阅读范围/).waitFor();
  await shot('after-report-narrow');await page.locator('[data-ref="panelClose"]').click();
  // Catalogue is a directory, not an assertion that every saved paper is cited.
  const catResponse=await page.request.get(`${base}/api/projects/${pid}/research-tasks/${task.task_id}/export?version=1&catalog=true`);assert.equal(catResponse.status(),200);assert.match(await catResponse.text(),/不是|不代表/);
  fs.writeFileSync(path.join(out,'offline-catalog-v1.md'),await catResponse.text());
  const pending=page.waitForEvent('download');await row.getByRole('link',{name:'保存文献目录',exact:true}).click();const actual=await pending;assert.match(fs.readFileSync(await actual.path(),'utf8'),/文献目录/);
  await page.reload();await ready();await page.locator('.ch-message-assistant').last().waitFor();
  assert.equal((await get(prefix+'/messages')).messages.filter(m=>m.research_task_id===task.task_id).length,1);
  assert.equal((await get('/__delivery__/state')).model_calls.length,modelCalls);
  await post('/__delivery__/mode',{mode:'ok'});await page.setViewportSize({width:1440,height:900});
  const old=page.locator(`.ch-task-card[data-task-id="${task.task_id}"]`);if(!await old.evaluate(n=>n.open))await old.locator(':scope>summary').click();await old.getByRole('button',{name:'补跑失败来源',exact:true}).click();
  for(let i=0;i<100;i++){task=await get(prefix+'/research-tasks/'+task.task_id);if(task.current_version===2&&task.status==='completed')break;await page.waitForTimeout(150);}
  assert.equal(task.current_version,2);assert.equal(await download(task.task_id,1),original,'v1 bytes and source mapping must survive resume');
  assert.equal(await download(task.task_id,2),task.report_markdown);
  await page.reload();await ready();const latest=page.locator(`.ch-task-card[data-task-id="${task.task_id}"]`);await latest.waitFor();if(!await latest.evaluate(n=>n.open))await latest.locator(':scope>summary').click();await latest.getByRole('button',{name:'下载 v1',exact:true}).waitFor();
  await shot('after-history-desktop');
  // A new report must not pull someone away from earlier messages.
  await post('/__delivery__/mode',{mode:'hold_synthesis'});
  await page.locator('[data-ref="input"]').fill('请检索联邦学习比较方法的文献，并整理研究现状');await page.locator('[data-ref="send"]').click();
  let held;
  for(let i=0;i<100;i++){held=(await get(prefix+'/research-tasks?limit=10')).tasks.find(t=>t.task_id!==task.task_id);if(held?.stage==='synthesizing')break;await page.waitForTimeout(100);}
  assert.ok(held,'explicit search must create a task');assert.equal(held.stage,'synthesizing');
  await page.locator('[data-ref="messages"]').evaluate(n=>n.scrollTop=0);
  await post('/__delivery__/release',{});
  for(let i=0;i<100;i++){held=await get(prefix+'/research-tasks/'+held.task_id);if(held.status==='completed')break;await page.waitForTimeout(100);}
  await page.locator(`.ch-message[data-request-id="${held.request_id}"]`).filter({has:page.locator('.ch-citation')}).waitFor();
  assert.ok(await page.locator('[data-ref="messages"]').evaluate(n=>n.scrollTop<30),'late delivery cannot force history reader to the bottom');
  // A separately labelled example whose bibliography is publicly verifiable.
  await post('/__delivery__/mode',{mode:'metadata'});
  const metaCid=(await post(`/api/projects/${pid}/conversations`,{title:'仅元信息的公开来源目录样例',entry_stage:'literature'})).conversation.conversation_id;
  const metaStart=await post(`/api/projects/${pid}/conversations/${metaCid}/research-tasks`,{request_id:'public-bibliography',content:'请整理联邦学习公开文献目录',topic:'联邦学习'});
  let meta;
  for(let i=0;i<100;i++){meta=await get(`/api/projects/${pid}/conversations/${metaCid}/research-tasks/${metaStart.task_id}`);if(['completed','partial','failed'].includes(meta.status))break;await page.waitForTimeout(150);}
  assert.equal(meta.status,'completed');assert.equal(meta.mode,'rules');
  const publicReport=await download(meta.task_id,1);assert.match(publicReport,/proceedings\.mlr\.press/);assert.match(publicReport,/arxiv\.org/);assert.match(publicReport,/仅元信息/);
  fs.writeFileSync(path.join(out,'offline-public-source-report.md'),'> 离线软件排版样例：书目信息复用预置案例；来源调用为本地替身，未进行本轮公网核验或模型内容分析，不是实时调研或科研结论。\n\n'+publicReport);
  await post('/__delivery__/mode',{mode:'bad_synthesis'});
  const badCid=(await post(`/api/projects/${pid}/conversations`,{title:'整理失败的目录交付',entry_stage:'literature'})).conversation.conversation_id;
  await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${badCid}`);await ready();
  await page.locator('[data-ref="input"]').fill('请检索联邦学习方法并整理研究现状');await page.locator('[data-ref="send"]').click();
  await page.locator('.ch-message-assistant').filter({hasText:'模型内容整理未成功'}).waitFor();
  assert.doesNotMatch(await page.locator('.ch-message-assistant').last().innerText(),/研究要点/);
  await shot('after-synthesis-failure-desktop');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,project_id:pid,conversation_id:cid,task_id:task.task_id,model_fixture_calls:(await get('/__delivery__/state')).model_calls.length,real_model_calls:0,public_source_calls:0,errors,external,version_1_unchanged:true},null,2));
  console.log('PASS delivery -> citation -> full report -> catalogue download -> refresh -> resume -> historical version; desktop/390px');
 }catch(e){await page.screenshot({path:path.join(out,'failure.png')});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
