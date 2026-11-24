/* Seven continuous journeys on the actual main app. SDK/source HTTP are offline fixtures. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_SECOND_URL || 'http://127.0.0.1:8065',out=path.resolve(process.env.SRA_SECOND_SHOTS || 'docs/second-batch-workspace-unification');
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const ctx=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await ctx.newPage(),errors=[],outside=[];
 page.on('pageerror',e=>errors.push(e.message));await ctx.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(r.request().url()),r.abort()));
 const api=page.request,get=async u=>{const r=await api.get(base+u);assert.equal(r.status(),200,await r.text());return r.json();};
 const post=async(u,data)=>{const r=await api.post(base+u,{data});assert.ok(r.ok(),await r.text());return r.json();};
 const route=()=>{const u=new URL(page.url());return {pid:u.searchParams.get('project'),cid:u.searchParams.get('conversation')};};
 const prefix=p=>`/api/projects/${p.pid}/conversations/${p.cid}`;
 const ready=async()=>{await page.locator('[data-ref="input"]').waitFor();await page.locator('[data-ref="send"]:not(:disabled)').waitFor();};
 const open=async(p={},name='dashboard')=>{await page.goto(`${base}/workspace?page=${name}${p.pid?'&project='+p.pid:''}${p.cid?'&conversation='+p.cid:''}`);if(name==='dashboard')await ready();else await page.locator('.workspace-unified').waitFor();};
 const shot=async name=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,name+'.png')});};
 const submit=async text=>{await page.locator('[data-ref="input"]').fill(text);await page.locator('[data-ref="send"]').click();};
 const answered=async()=>{await page.locator('.ch-message-assistant').last().waitFor();await ready();};
 const chooseFile=async()=>{const chooser=page.waitForEvent('filechooser');await page.locator('[data-ref="import"]').click();const c=await chooser;await c.setFiles({name:'federated-notes.txt',mimeType:'text/plain',buffer:Buffer.from('Offline uploaded research material. Federated learning compares methods under different client data. No experimental result. '.repeat(12))});};
 const results={};
 try{
  const count=(await get('/api/projects?limit=5')).total;
  await open();await page.locator('#ch-new').click();await ready();
  await chooseFile();await page.getByText('待提交资料：federated-notes.txt',{exact:true}).waitFor();await page.getByRole('button',{name:'移除待上传文件',exact:true}).click();
  assert.equal((await get('/api/projects?limit=5')).total,count,'Selecting and removing files must not create projects');
  await submit('我是第一次做科研，我想研究联邦学习中的数据差异，只有笔记本和四周。');
  await page.getByRole('combobox',{name:'确认研究领域',exact:true}).waitFor();assert.equal(await page.locator('.ch-settings').getAttribute('open'),null);
  await page.getByRole('combobox',{name:'确认研究领域',exact:true}).selectOption('ai');
  // Create committed, response lost. The repeated request must recover one identity.
  let lost=true,starts=0;
  await page.route('**/api/conversations/start',async r=>{starts++;const response=await r.fetch();if(lost){lost=false;return r.abort('failed');}return r.fulfill({response});});
  await page.getByRole('button',{name:'确认并继续提交',exact:true}).click();await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
  assert.match(await page.locator('[data-ref="input"]').inputValue(),/联邦学习/);
  await page.reload();await ready();await page.locator('[data-ref="send"]').click();await answered();const A=route();
  assert.equal((await get('/api/projects?limit=5')).total,count+1);assert.equal((await get(prefix(A)+'/messages')).messages.filter(m=>m.role==='user').length,1);
  assert.match(await page.locator('[data-ref="project"]').innerText(),/联邦学习/);assert.doesNotMatch(await page.locator('[data-ref="title"]').innerText(),/我是第一次/);await shot('new-research-desktop');await page.unroute('**/api/conversations/start');
  results.from_zero={passed:true,starts,project:A};
  // New research clears current project. Pending file first-send creates once.
  await page.locator('#ch-new').click();await ready();assert.equal(route().pid,null);await chooseFile();
  await page.locator('[data-ref="input"]').fill('基于这份论文资料讨论研究问题，未知条件保持待确认。');
  let failed=true,uploadPosts=0;await page.route('**/api/projects/*/upload-paper',async r=>{uploadPosts++;if(failed){failed=false;return r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'离线注入：上传暂不可用'})});}const response=await r.fetch();return r.fulfill({response});});
  await page.locator('[data-ref="send"]').click();await page.getByRole('combobox',{name:'确认研究领域',exact:true}).selectOption('ai');await page.getByRole('button',{name:'确认并继续提交',exact:true}).click();
  await page.getByText('离线注入：上传暂不可用',{exact:true}).waitFor();const B=route();assert.ok(B.pid && B.cid);assert.equal((await get('/api/projects?limit=5')).total,count+2);
  assert.match(await page.locator('[data-ref="input"]').inputValue(),/论文资料/);await page.locator('[data-ref="send"]').click();await answered();await page.unroute('**/api/projects/*/upload-paper');
  const papers=await get(`/api/projects/${B.pid}/papers`);assert.equal(papers.papers.length,1);assert.equal((await get('/api/projects?limit=5')).total,count+2);await shot('paper-start-desktop');results.paper_start={passed:true,uploadPosts,project:B};
  // Current project new conversation keeps shared material, not its chat history.
  await page.locator('#ch-project-new').click();await ready();assert.equal(route().pid,B.pid);assert.equal(route().cid,null);assert.equal(await page.locator('.ch-message-assistant').count(),0);
  await page.locator('[data-ref="input"]').fill('本项目另一段讨论草稿');await open(A);await page.locator('[data-ref="input"]').fill('A 未提交草稿');await open({pid:B.pid});assert.equal(await page.locator('[data-ref="input"]').inputValue(),'本项目另一段讨论草稿');
  await page.locator('[data-ref="send"]').click();await answered();const B2=route();assert.notEqual(B2.cid,B.cid);assert.equal((await get(prefix(B2)+'/messages')).messages.filter(m=>m.role==='user').length,1);assert.equal((await get(`/api/projects/${B2.pid}/workspace?include_chat=false`)).papers.length,1);
  results.multi_conversation={passed:true,second:B2};
  // Teaching new-chat entry is a choice, not a nonfunctional composer.
  const caseStart=await post('/api/teaching-cases/start',{request_id:'batch2-case',case_id:'fl-heterogeneity',version:'1.1.0'}),C={pid:caseStart.project_id,cid:caseStart.conversation_id};
  await open(C);await page.locator('#ch-project-new').click();await page.getByRole('button',{name:'继续当前案例',exact:true}).waitFor();assert.equal(await page.locator('[data-ref="form"]').isVisible(),false);
  await page.getByRole('button',{name:'带着这个思路开始自己的研究',exact:true}).last().click();
  await page.locator('.case-fork input[name="title"]').fill('自己的联邦学习研究');await page.locator('.case-fork input[name="resources"]').fill('我的普通笔记本 CPU');await page.locator('.case-fork input[name="time_budget"]').fill('四周');
  await page.getByRole('button',{name:'确认创建普通研究',exact:true}).click();await ready();const own=route();await page.getByText('已从案例带入参考资料',{exact:true}).waitFor();
  const ownWorkspace=await get(`/api/projects/${own.pid}/workspace?include_chat=false`);assert.equal(ownWorkspace.workflow.flow.question,null);assert.equal(ownWorkspace.workflow.flow.planReady,false);assert.equal(ownWorkspace.workspace.brief.resources,'我的普通笔记本 CPU');await shot('teaching-handoff-desktop');results.teaching_transfer={passed:true,project:own};
  // Actual offline source boundary generates a partial report through normal conversation.
  await open(B);await post('/__reliability__/mode',{source_mode:'partial'});await submit('请检索联邦学习非IID方法比较的文献并整理研究现状');
  let task;for(let i=0;i<100;i++){const list=await get(prefix(B)+'/research-tasks?limit=10');task=list.tasks[0];if(task&&['completed','partial','failed'].includes(task.status))break;await page.waitForTimeout(200);}
  assert.equal(task.status,'partial');await page.locator('.ch-task-card').filter({hasText:'部分完成'}).waitFor();
  await open(B,'research-report');await page.locator('[data-material-key^="research-task:"]').waitFor();const report=page.locator('[data-material-key^="research-task:"]').first();assert.match(await report.innerText(),/部分完成/);await report.locator('summary').click();await report.locator('.research-document').waitFor().catch(()=>{});
  const downloadPromise=page.waitForEvent('download');await report.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();const downloaded=await downloadPromise;const reportText=fs.readFileSync(await downloaded.path(),'utf8');assert.match(reportText,/部分完成|来源失败/);fs.writeFileSync(path.join(out,'report-download.md'),reportText);
  const material=await get(`/api/projects/${B.pid}/workspace/materials?kind=reports`),exported=await get(`/api/projects/${B.pid}/exports`);assert.equal(material.entries[0].url,exported.entries.find(e=>e.key===material.entries[0].key).url);await shot('report-desktop');
  await open(B,'progress');await page.locator(`[data-activity-id="${task.task_id}"]`).waitFor();assert.match(await page.locator(`[data-activity-id="${task.task_id}"]`).innerText(),/部分完成/);await shot('activities-desktop');await page.locator(`[data-activity-id="${task.task_id}"]`).getByRole('button',{name:'返回对应对话',exact:true}).click();await ready();assert.deepEqual(route(),B);results.report_consistency={passed:true,task:task.task_id};
  // Generate, review, adopt and read the same plan. No experiment execution.
  await open(own);await submit('请基于已有摘要提出一个可讨论的问题，不生成方案。');await answered();await page.getByRole('button',{name:'采用这个问题',exact:true}).last().click();await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
  await page.locator('.ch-plan-tools summary').click();await page.getByRole('combobox',{name:'方案研究方法',exact:true}).selectOption('computational');await page.getByRole('button',{name:'准备生成方案请求',exact:true}).click();await page.locator('[data-ref="send"]').click();
  await page.locator('.ch-proposal-preview[data-plan-preview-id]').last().waitFor();await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();const review=page.locator('.ch-plan-review[open]').last();await review.getByRole('button',{name:'采纳方案',exact:true}).click();await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
  await open(own,'experiments');await page.getByText('当前正式方案',{exact:false}).first().waitFor();let w=await get(`/api/projects/${own.pid}/workspace?include_chat=false`);assert.equal(w.workflow.flow.planReady,true);assert.match(await page.locator('.workspace-unified').innerText(),/方案可审阅/);await shot('plan-current-desktop');
  const currentId=w.workflow.flow.plan.item_id;const row=page.locator(`[data-material-key="plan:${currentId}"]`);const dp=page.waitForEvent('download');await row.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();const d=await dp;assert.match(fs.readFileSync(await d.path(),'utf8'),/当前正式方案/);
  const projectList=await get('/api/projects?limit=100');assert.equal(projectList.projects.find(p=>p.project_id===own.pid).research_progress.planReady,true);await open(own,'progress');assert.ok((await get(`/api/projects/${own.pid}/activities`)).activities.some(a=>a.kind==='plan'&&a.status==='completed'));
  const rev=w.workspace.revision;const response=await api.put(base+`/api/projects/${own.pid}/workspace/brief`,{data:{expected_revision:rev,brief:{...w.workspace.brief,time_budget:'两周'}}});assert.ok(response.ok(),await response.text());await open(own,'experiments');await page.getByText(/已确认进展：材料待复核/).waitFor();results.plan_consistency={passed:true,item:currentId,review_after_change:true};
  // Deep links/history, narrow screen, no old restore/start actions.
  await page.setViewportSize({width:390,height:844});await open(B,'research-report');await page.locator('[data-material-key^="research-task:"]').first().waitFor();await shot('report-mobile');await open(own,'experiments');await page.locator('[data-material-key^="plan:"]').waitFor();await shot('plan-mobile');
  await open(B2);await ready();await page.locator('[data-ref="input"]').fill('窄屏保留草稿');await page.reload();await ready();assert.equal(await page.locator('[data-ref="input"]').inputValue(),'窄屏保留草稿');await shot('conversation-mobile');
  await page.goBack();await page.goForward();await ready();assert.deepEqual(route(),B2);await open(B,'progress');await page.locator(`[data-activity-id="${task.task_id}"]`).waitFor();assert.equal(await page.getByRole('button',{name:'继续当前任务',exact:true}).count(),0);await shot('activities-mobile');results.history_and_errors={passed:true};
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);results.errors=errors;results.external_browser_requests=outside;results.boundary='offline SDK and source fixture; no real model acceptance';fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));console.log('PASS seven journeys',JSON.stringify(results));
 }catch(e){await page.screenshot({path:path.join(out,'failure.png')});console.error('URL',page.url());throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
