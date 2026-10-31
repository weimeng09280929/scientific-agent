/* Actual main app/FastAPI/temp SQLite, only model/source boundaries replaced. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8068',out=path.resolve('docs/research-delivery-closure/supplement-20261026');
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await context.newPage();
 const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
 const api=async(url,data)=>{let r=data===undefined?await page.request.get(base+url):await page.request.post(base+url,{data});assert.ok(r.ok(),await r.text());return r.json();};
 const shot=async(name)=>{assert.ok(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));await page.screenshot({path:path.join(out,name+'.png')});};
 let pid;
 try{
  pid=(await api('/__fixture__/state')).project_id;
  async function run(mode,title){
   await api('/__delivery__/mode',{mode});
   const cid=(await api(`/api/projects/${pid}/conversations`,{title,entry_stage:'literature'})).conversation.conversation_id;
   const prefix=`/api/projects/${pid}/conversations/${cid}`;
   await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`);
   await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
   await page.locator('[data-ref="input"]').fill('请检索联邦学习方法比较的代表性文献，并整理研究现状');await page.locator('[data-ref="send"]').click();
   let task;for(let i=0;i<120;i++){task=(await api(prefix+'/research-tasks')).tasks[0];if(task&&['completed','partial','failed'].includes(task.status))break;await page.waitForTimeout(100);}
   assert.equal(task.status,'completed',task.error);await page.locator('.ch-message-assistant').last().waitFor();
   const message=(await api(prefix+'/messages')).messages.find(m=>m.research_task_id===task.task_id);
   const row=page.locator(`[data-message-id="${message.message_id}"]`);await row.waitFor();
   return {cid,prefix,task,message,row};
  }
  const cross=await run('cross_reference','正文交叉引用离线验收');
  assert.equal(cross.message.cited_paper_ids.length,2);assert.equal(cross.message.report_citations.length,2);
  assert.match(await cross.row.innerText(),/\[2\]/);assert.doesNotMatch(await cross.row.innerText(),/引用未解析/);
  await shot('cross-dialogue-desktop');await page.setViewportSize({width:390,height:844});await shot('cross-dialogue-narrow');
  await cross.row.locator('.ch-citation').filter({hasText:'[2]'}).click();await page.locator('[data-ref="panel"]:not([hidden])').waitFor();
  await page.locator('[data-ref="materialBody"]').getByText(/Fixture/).first().waitFor();await page.locator('[data-ref="panelClose"]').click();
  await cross.row.getByRole('button',{name:'打开完整报告',exact:true}).click();const body=page.locator('[data-ref="materialBody"]');
  await body.getByText('参考文献与阅读范围',{exact:true}).waitFor();
  assert.doesNotMatch(await body.innerText(),/引用未解析/);await shot('cross-report-narrow');
  await body.getByText('参考文献与阅读范围',{exact:true}).scrollIntoViewIfNeeded();await shot('cross-bibliography-narrow');
  await page.setViewportSize({width:1440,height:900});await body.getByText('参考文献与阅读范围',{exact:true}).scrollIntoViewIfNeeded();await shot('cross-bibliography-desktop');
  const r=await page.request.get(base+`/api/projects/${pid}/research-tasks/${cross.task.task_id}/export?version=1`);const report=await r.text();
  fs.writeFileSync(path.join(out,'cross-report-v1.md'),report);assert.match(report,/\[2\] Fixture/);
  await page.reload();await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
  assert.equal((await api(cross.prefix+'/messages')).messages.filter(m=>m.research_task_id===cross.task.task_id).length,1);
  await page.setViewportSize({width:1440,height:900});
  const failed=await run('bad_synthesis','内容整理失败的目录验收');
  assert.equal(failed.task.content_status,'synthesis_failed_catalog_only');const label=failed.task.status_label;
  const material=(await api(`/api/projects/${pid}/workspace/materials?kind=reports`)).entries.find(e=>e.task_id===failed.task.task_id);
  const exported=(await api(`/api/projects/${pid}/exports`)).entries.find(e=>e.task_id===failed.task.task_id);
  assert.equal(material.status_label,label);assert.equal(exported.status_label,label);
  assert.match(await page.locator(`.ch-task-card[data-task-id="${failed.task.task_id}"] > summary`).innerText(),/模型内容整理未成功/);
  await page.locator('[data-ref="downloadToggle"]').click();await page.locator('[data-ref="downloadBody"]').getByText(label,{exact:true}).waitFor();
  await shot('failed-download-menu-desktop');await page.locator('[data-ref="downloadToggle"]').click();
  await failed.row.getByRole('button',{name:'打开完整报告',exact:true}).click();await body.getByText('参考文献与阅读范围',{exact:true}).waitFor();
  const selected=body.locator(`.ch-report-card[data-report-id="${failed.task.task_id}"]`);
  assert.ok((await selected.locator(':scope>summary').innerText()).includes(label));assert.ok((await selected.innerText()).includes('不是正常研究内容综合'));
  await shot('failed-material-preview-desktop');await page.setViewportSize({width:390,height:844});await shot('failed-material-preview-narrow');
  const pending=page.waitForEvent('download');await selected.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();const file=await pending;const downloaded=fs.readFileSync(await file.path(),'utf8');
  assert.ok(downloaded.includes(label));fs.writeFileSync(path.join(out,'failed-report-v1.md'),downloaded);
  await page.locator('[data-ref="panelClose"]').click();await page.locator('[data-ref="downloadToggle"]').click();await page.locator('[data-ref="downloadBody"]').getByText(label,{exact:true}).waitFor();await shot('failed-download-menu-narrow');
  await page.keyboard.press('Escape');assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,project_id:pid,cross_task:cross.task.task_id,failed_task:failed.task.task_id,content_label:label,real_model_calls:0,public_source_calls:0,errors,external,fixture_calls:(await api('/__delivery__/state')).model_calls.length},null,2));
  console.log('PASS cross references + directory/preview/download content states, desktop/390px, actual main app');
 }catch(e){await page.screenshot({path:path.join(out,'failure.png')});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
