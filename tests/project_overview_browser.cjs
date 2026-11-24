/* Real account UI against the explicitly isolated overview fixture only. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8096',out=path.resolve('docs/project-overview-entry'),before=process.argv.includes('--before');
const passwords=['Offline restore sample A!','Offline restore sample B!'];
const ready=p=>p.locator('[data-ref="send"]:not(:disabled)').waitFor();
async function login(p,index){await p.goto(base);await p.locator('input[name=email]').fill(index?'backup-b@example.com':'backup-a@example.com');await p.locator('input[name=password]').fill(passwords[index]);await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
async function overview(p){await p.locator('.project-overview[data-loaded="true"]').waitFor();}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],external=[],writes=[];let releaseHeld=null;
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!['GET','HEAD'].includes(r.method())&&/^\/api\/(projects|conversations)/.test(new URL(r.url()).pathname))writes.push(r.method()+' '+new URL(r.url()).pathname);});
 try{
  const initial=await (await page.request.get(base+'/__overview__/state')).json(),a=initial.sample.users[0],b=initial.sample.users[1];
  const url=record=>`${base}/?page=dashboard&project=${record.project_id}&conversation=${record.conversation_id}`;
  await login(page,0);await page.goto(url(a));await ready(page);
  await page.locator('#ch-current-project').filter({hasText:'人工智能RAG'}).waitFor();
  await page.locator('#ch-input').fill('尚未提交的原对话输入，必须保留。');
  await page.locator('#ch-current-project').click();
  if(before){
   await ready(page);assert.equal(new URL(page.url()).searchParams.get('page'),'dashboard');
   assert.equal(new URL(page.url()).searchParams.get('conversation'),null);
   assert.equal(await page.getByRole('heading',{name:'项目总览',exact:true}).count(),0);
   await page.screenshot({path:path.join(out,'before-desktop.png'),fullPage:true});
   const after=await(await page.request.get(base+'/__overview__/state')).json();assert.deepEqual(after.snapshot,initial.snapshot);assert.deepEqual(writes,[]);
   fs.writeFileSync(path.join(out,'before-results.json'),JSON.stringify({reproduced:true,actualPage:'dashboard',conversationCleared:true,noOverviewHeading:true,readOnly:true},null,2));return;
  }
  await overview(page);assert.equal(new URL(page.url()).searchParams.get('conversation'),a.conversation_id);
  assert.match(await page.locator('.project-overview').innerText(),/人工智能RAG/);
  const text=await page.locator('.project-overview').innerText();
  for(const s of ['项目总览','检索增强生成','已有资料与成果','已入库 1 篇','已保存 1 条笔记','已明确采纳','未采纳草案','四周','只有 CPU，没有 GPU'])assert(text.includes(s),s);
  await page.screenshot({path:path.join(out,'overview-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'返回刚才的对话',exact:true}).click();await ready(page);
  assert.equal(await page.locator('#ch-input').inputValue(),'尚未提交的原对话输入，必须保留。');
  // The browser-owned File remains in the existing per-conversation memory map.
  await page.locator('[data-ref="import"]').click();
  const [chooser]=await Promise.all([page.waitForEvent('filechooser'),page.getByRole('button',{name:'添加待上传论文',exact:true}).click()]);
  await chooser.setFiles(path.join(initial.root,'selection.pdf'));
  await page.locator('[data-ref="filePending"]').filter({hasText:'selection.pdf'}).waitFor();
  await page.locator('#ch-current-project').click();await overview(page);await page.goBack();await ready(page);
  assert.equal(await page.locator('#ch-input').inputValue(),'尚未提交的原对话输入，必须保留。');
  assert.match(await page.locator('[data-ref="filePending"]').innerText(),/selection.pdf/);
  assert(!/重新选择/.test(await page.locator('[data-ref="filePending"]').innerText()));
  await page.goForward();await overview(page);await page.reload();await overview(page);
  await page.getByRole('button',{name:'返回刚才的对话',exact:true}).click();await ready(page);
  assert.equal(await page.locator('#ch-input').inputValue(),'尚未提交的原对话输入，必须保留。');
  assert.match(await page.locator('[data-ref="filePending"]').innerText(),/重新选择|重选/);
  await page.locator('#ch-current-project').click();await overview(page);
  // Material links open existing readers, then return without overwriting drafts.
  await page.getByRole('button',{name:'查看文献与资料',exact:true}).click();await page.locator('.paper-card,[data-paper-id]').first().waitFor();
  await page.goBack();await overview(page);
  await page.getByRole('button',{name:'审阅正式方案',exact:true}).first().click();await ready(page);await page.locator('[data-ref="panel"]').waitFor({state:'visible'});
  await page.goBack();await overview(page);
  await page.getByRole('button',{name:'在本项目新建对话',exact:true}).last().click();await ready(page);
  assert.equal(new URL(page.url()).searchParams.get('conversation'),null);
  await page.goBack();await overview(page);
  // Empty project has no conversation: viewing doesn't create one.
  await page.goto(`${base}/?page=project-overview&project=${a.empty_project_id}`);await overview(page);
  assert.match(await page.locator('.project-overview').innerText(),/项目简介尚未完善，可以在对话中逐步明确研究目标与条件。/);
  assert.match(await page.locator('.project-overview').innerText(),/尚无对话/);
  await page.screenshot({path:path.join(out,'empty-desktop.png'),fullPage:true});
  // A real failed GET must show a failure and an effective retry, never empty success.
  let fail=true;await page.route('**/workspace?include_chat=false',r=>fail?r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'离线注入的材料读取失败'})}):r.continue());
  await page.goto(`${base}/?page=project-overview&project=${a.project_id}&conversation=${a.conversation_id}`);
  await page.getByText('项目总览暂时无法读取',{exact:true}).waitFor();assert.equal(await page.locator('.project-overview [data-section="materials"]').count(),0);
  await page.screenshot({path:path.join(out,'read-failure-desktop.png'),fullPage:true});
  fail=false;await page.getByRole('button',{name:'重新读取项目总览',exact:true}).click();await overview(page);await page.unroute('**/workspace?include_chat=false');
  // A late response from A's other project cannot paint the empty project.
  let release;const held=new Promise(r=>release=r);releaseHeld=release;let entered;const captured=new Promise(r=>entered=r);
  await page.route('**/workspace?include_chat=false',async r=>{if(!r.request().url().includes(`/projects/${a.project_id}/`))return r.continue();const response=await r.fetch();entered();await held;await r.fulfill({response}).catch(()=>{});});
  await page.getByRole('button',{name:'刷新项目总览',exact:true}).click();await captured;
  await page.locator(`.ch-project-link[data-project-id="${a.empty_project_id}"]`).click();await overview(page);
  await page.locator('#ch-current-project').click();release();await page.unroute('**/workspace?include_chat=false');await overview(page);
  assert.equal(await page.locator('.project-overview h1').innerText(),'空项目');
  // An independent B session cannot read A, even with known project/conversation IDs.
  const cb=await browser.newContext({viewport:{width:390,height:844}}),pb=await cb.newPage();await login(pb,1);
  assert.equal((await pb.request.get(`${base}/api/projects/${a.project_id}/workspace?include_chat=false`)).status(),404);
  await pb.goto(`${base}/?page=project-overview&project=${b.project_id}&conversation=${b.conversation_id}`);await overview(pb);
  assert.equal(await pb.locator('.project-overview h1').innerText(),'B私有研究');
  assert.equal(await pb.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await pb.screenshot({path:path.join(out,'overview-390px.png'),fullPage:true});
  await pb.getByRole('button',{name:'返回刚才的对话',exact:true}).click();await ready(pb);assert.equal(await pb.locator('#ch-input').inputValue(),'');
  await pb.locator('#ch-menu').click();await pb.locator('#ch-current-project').click();await overview(pb);
  assert.equal(await pb.locator('.project-overview h1').innerText(),'B私有研究');
  await cb.close();
  // Same browser switching accounts must clear the old protected view.
  await page.locator('#account-logout').click();await page.locator('#account-gate').waitFor({state:'visible'});await login(page,1);
  await page.goto(`${base}/?page=project-overview&project=${b.project_id}`);await overview(page);assert.equal(await page.locator('.project-overview h1').innerText(),'B私有研究');
  assert(!await page.locator('.project-overview').innerText().then(t=>t.includes('人工智能RAG')));
  const final=await(await page.request.get(base+'/__overview__/state')).json();
  assert.deepEqual(final.snapshot,initial.snapshot);assert.deepEqual(final.calls,initial.calls);assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,scenarios:13,readOnlyProjectHashes:true,noNewConversations:true,noGeneration:true,draftAndFilePreserved:true,reloadRequiresFileReselection:true,history:true,empty:true,readFailureRetry:true,accountIsolation:true,desktopAnd390:true,errors,external,writes},null,2));
  console.log('PASS overview: read-only materials, drafts/files, history, failure/retry, dual account, desktop/390px');
 }catch(e){await page.screenshot({path:path.join(out,`failure-${Date.now()}.png`),fullPage:true}).catch(()=>{});throw e;}finally{releaseHeld?.();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
