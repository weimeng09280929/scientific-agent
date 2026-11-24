/* Actual primary app, FastAPI and temporary SQLite. No model/source business-route mocks. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_LIBRARY_URL||'http://127.0.0.1:8069',out=path.resolve('docs/sidebar-library-audit-fixes');
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await ctx.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const get=async u=>{const r=await page.request.get(base+u);assert.ok(r.ok(),await r.text());return r.json();};
 const post=async(u,data)=>{const r=await page.request.post(base+u,{data});assert.ok(r.ok(),await r.text());return r.json();};
 const make=async title=>post('/api/conversations/start',{request_id:crypto.randomUUID(),title,domain:'ai'});
 const A=await make('文献徽标和任务审计验收'),B=await make('独立空项目');
 const root=`/api/projects/${A.project_id}`,conv=root+`/conversations/${A.conversation_id}`;
 const open=async(p,kind='dashboard')=>{await page.goto(`${base}/workspace?page=${kind}&project=${p.project_id}&conversation=${p.conversation_id}`);await page.locator(kind==='dashboard'?'[data-ref="send"]:not(:disabled)':'h2.page-title').waitFor();};
 const badge=async n=>page.waitForFunction(n=>{const x=document.getElementById('badge-papers');return x.style.display!=='none'&&x.textContent===String(n);},n);
 const done=async()=>page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled&&!document.querySelector('.ch-request-status'),{},{timeout:30000});
 const checkLink=async()=>assert.ok(await page.locator('#ch-project-new').evaluate(n=>{const r=n.getBoundingClientRect(),s=n.closest('.sidebar').getBoundingClientRect();return r.x>=s.x+12&&r.right<=s.right-10&&r.height>=32&&n.scrollWidth<=n.clientWidth;}));
 try{
  await post('/__reliability__/mode',{guide_mode:'slow',source_mode:'ok'});
  await open(A);await badge(0);await checkLink();
  const chooser=page.waitForEvent('filechooser');await page.locator('[data-ref="import"]').click();await page.getByRole('button',{name:'添加待上传论文',exact:true}).click();await(await chooser).setFiles({name:'offline-library.txt',mimeType:'text/plain',buffer:Buffer.from('Saved offline source, not an experimental finding. '.repeat(30))});
  await page.locator('[data-ref="input"]').fill('请根据我导入的资料讨论范围，不检索。');await page.locator('[data-ref="send"]').click();
  // Count must be delivered BEFORE the assistant responds, without visiting Papers.
  await badge(1);assert.match(page.url(),/page=dashboard/);
  assert.equal((await get(root+'/papers')).papers.length,1);
  await post('/__reliability__/release',{});await done();
  await page.screenshot({path:path.join(out,'sidebar-upload-desktop.png')});
  await post('/__reliability__/mode',{guide_mode:'ok'});
  await page.locator('[data-ref="input"]').fill('请检索联邦学习非IID方法比较的文献并整理研究现状');await page.locator('[data-ref="send"]').click();
  let task;
  for(let i=0;i<100;i++){task=(await get(conv+'/research-tasks?limit=10')).tasks[0];if(task&&['completed','partial','failed'].includes(task.status))break;await page.waitForTimeout(200);}
  assert.equal(task.status,'completed');
  const count=(await get(root+'/papers')).papers.length;await badge(count);
  await open(A,'papers');
  const row=page.locator('.paper-search-audit').filter({hasText:task.topic});await row.waitFor();await row.locator(':scope>summary').click();
  assert.match(await row.innerText(),/候选 4 · 纳入 1 · 已返回但排除 3 · 新入库 4/);
  await row.getByText('来源调用记录（2 条）',{exact:true}).click();
  assert.match(await row.innerText(),/arxiv.*成功无结果/);assert.match(await row.innerText(),/openalex.*成功返回/);
  const download=page.waitForEvent('download');await row.getByRole('link',{name:'下载对应报告'}).click();assert.match(fs.readFileSync(await(await download).path(),'utf8'),/筛选排除/);
  await page.screenshot({path:path.join(out,'library-audit-desktop.png')});
  await row.getByRole('button',{name:'查看原对话任务'}).click();await done();assert.equal(new URL(page.url()).searchParams.get('conversation'),A.conversation_id);
  const card=page.locator('.ch-task-card').filter({hasText:task.topic});await card.locator(':scope>summary').click();await card.getByRole('button',{name:'查看检索审计',exact:true}).click();
  assert.equal(await card.locator('.ch-task-diagnostics').evaluate(n=>n.open),true);
  assert.equal(await card.locator('.ch-task-detail').evaluate(n=>n.open),true);
  await page.screenshot({path:path.join(out,'conversation-audit-desktop.png')});
  await open(B);await badge(0);
  await page.evaluate(pid=>{window.dispatchEvent(new CustomEvent('sra:workspace-loaded',{detail:{projectId:pid,paper_count:99,counts:{}}}));window.dispatchEvent(new CustomEvent('sra:papers-changed',{detail:{projectId:pid}}));},A.project_id);
  await badge(0);
  // Recovery of a committed upload after response loss refreshes the library count.
  await open(B,'papers');await page.locator('.paper-import>summary').click();
  await page.locator('.paper-import input[type=file]').setInputFiles({name:'lost.txt',mimeType:'text/plain',buffer:Buffer.from('Offline recovered import. '.repeat(20))});
  await page.route('**/upload-paper',async r=>{await r.fetch();await r.abort('failed');});
  await page.getByRole('button',{name:'上传文献',exact:true}).click();await page.getByText(/^响应未确认：/).waitFor();await page.unroute('**/upload-paper');
  await page.getByRole('button',{name:'核对上次上传',exact:true}).click();await badge(1);
  assert.equal((await get(`/api/projects/${B.project_id}/papers`)).papers.length,1);
  await page.setViewportSize({width:390,height:844});await open(A,'papers');await page.locator('.paper-search-audit>summary').first().click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:path.join(out,'library-audit-mobile.png')});
  await page.locator('#ch-menu').click();await checkLink();await page.screenshot({path:path.join(out,'sidebar-mobile.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,upload_badge_before_reply:true,task_completion_badge:true,stale_project_ignored:true,response_loss_recovery:true,source_calls:task.source_calls.length,papers:count,errors,real_model_calls:0,real_source_calls:0},null,2));
  console.log('PASS sidebar layout, upload/task badge refresh, audit/read/export, stale project isolation, recovery, desktop/mobile');
 }catch(e){await page.screenshot({path:path.join(out,'failure.png')});throw e;}finally{await post('/__reliability__/release',{});await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
