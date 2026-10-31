/* Isolated folder/home journeys. All business routes are the real application. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8097',out=path.resolve(process.env.SRA_ORGANIZATION_OUTPUT||'docs/project-organization-home');
const ready=p=>p.locator('[data-ref="send"]:not(:disabled)').waitFor();
const folder=(p,id)=>p.locator(`.ch-folder[data-project-id="${id}"]`);
const passwords=['Offline restore sample A!','Offline restore sample B!'];
async function login(p,i){await p.goto(base);await p.locator('input[name=email]').fill(i?'backup-b@example.com':'backup-a@example.com');await p.locator('input[name=password]').fill(passwords[i]);await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
const current=p=>new URL(p.url()).searchParams;
const state=async p=>(await p.request.get(base+'/__organization__/state')).json();
async function chooseFile(p,file,home=false){
 if(home){const [chooser]=await Promise.all([p.waitForEvent('filechooser'),p.locator('[data-home-action=upload]').click()]);await chooser.setFiles(file);}
 else {await p.locator('[data-ref=import]').click();const [chooser]=await Promise.all([p.waitForEvent('filechooser'),p.getByRole('button',{name:'添加待上传论文',exact:true}).click()]);await chooser.setFiles(file);}
 await p.locator('[data-ref=filePending]').filter({hasText:'selection.pdf'}).waitFor();
}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}}),page=await ctx.newPage();
 page.setDefaultTimeout(15000);const errors=[],outside=[],writes=[],reads=[];let releaseHeld=null;
 async function guard(context){await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(new URL(r.request().url()).origin),r.abort()));}
 await guard(ctx);page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const u=new URL(r.url());if(u.pathname.startsWith('/api/')){if(!['GET','HEAD'].includes(r.method())&&!u.pathname.startsWith('/api/auth/'))writes.push(r.method()+' '+u.pathname);if(r.method()==='GET')reads.push(u.pathname+u.search);}});
 try{
  const initial=await state(page),sample=initial.sample.organization,pid=sample.first_project,other=sample.second_project,blank=sample.blank_project;
  const first=sample.first_project_conversations.find(c=>c.title==='同名对话'),second=sample.second_project_conversations.find(c=>c.title==='同名对话');
  await login(page,0);await folder(page,pid).waitFor();await folder(page,other).waitFor();
  assert.equal(reads.filter(s=>s.includes('/conversations?limit=5')).length,0);
  const homeText=await page.locator('.ch-welcome').innerText();
  for(const s of ['从一个科研兴趣','科研新手','说说兴趣与条件','逐步明确研究方向','导入或检索文献','结合资料讨论','创新点候选','形成方法草案','具体使用范围以实际读取内容为准'])assert(homeText.includes(s),s);
  assert(!/案例体验尚未接入|自动完成研究|保证具有创新/.test(homeText));
  await page.screenshot({path:path.join(out,'home-desktop.png'),fullPage:true});
  if(process.argv.includes('--catalog-only')){
   await page.locator('[data-teaching=directory]').click();await page.locator('.case-card').first().waitFor();
   assert((await page.getByRole('button',{name:'开始体验',exact:true}).count())>=1);
   await page.screenshot({path:path.join(out,'case-catalog-desktop.png'),fullPage:true});await page.getByRole('button',{name:'返回对话',exact:true}).click();
   const after=await state(page);assert.deepEqual(after.snapshot,initial.snapshot);assert.deepEqual(after.calls,initial.calls);assert.deepEqual(writes,[]);
   fs.writeFileSync(path.join(out,'catalog-results.json'),JSON.stringify({passed:true,existingCaseDirectoryUsable:true,noStartOrGeneration:true},null,2));return;
  }
  if(process.argv.includes('--visual-only')){
   await page.setViewportSize({width:1440,height:1100});
   for(const id of [pid,other]){await folder(page,id).locator('.ch-folder-toggle').click();await folder(page,id).locator('.ch-folder-chat').first().waitFor();}
   await page.locator('#ch-recent-group').click();await page.locator('.ch-history-scroll').evaluate(n=>n.scrollTop=0);
   await page.screenshot({path:path.join(out,'folders-structure-desktop.png'),fullPage:true});
   await folder(page,pid).locator(`.ch-folder-chat[data-conversation-id="${first.conversation_id}"] .ch-conversation-link`).click();await ready(page);
   await folder(page,pid).locator('.ch-conversation-link[aria-current=page]').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'folders-selected-desktop.png'),fullPage:true});
   assert.equal(await page.locator('.ch-current-project .ch-nav-name').innerText(),'人工智能RAG');
   await page.locator('#ch-new').click();await ready(page);await page.setViewportSize({width:390,height:844});await page.reload();await ready(page);
   assert.equal(await page.locator('[data-ref=send]').isEnabled(),true);await page.screenshot({path:path.join(out,'home-390px.png'),fullPage:true});
   await page.locator('.ch-home-flow summary').click();await page.locator('.ch-home-flow').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'home-flow-390px.png'),fullPage:true});
   await page.locator('#ch-menu').click();await folder(page,other).locator('.ch-folder-chat').first().waitFor();
   const long=folder(page,other).locator('.ch-conversation-link').filter({hasText:'这是一段需要完整辨认的长名称对话'});await long.focus();
   await page.screenshot({path:path.join(out,'folders-390px.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   const after=await state(page);assert.deepEqual(after.snapshot,initial.snapshot);assert.deepEqual(after.calls,initial.calls);
   fs.writeFileSync(path.join(out,'visual-results.json'),JSON.stringify({passed:true,readOnly:true,desktop1440x1100:true,narrow390x844:true,sendAvailable:true,longNamesReadable:true,errors},null,2));return;
  }
  // Folder expansion is separate from project navigation, and keyboard operable.
  await folder(page,pid).locator('.ch-folder-toggle').focus();await page.keyboard.press('Enter');
  await folder(page,pid).locator('.ch-folder-chat').first().waitFor();
  assert.equal(await folder(page,pid).locator('.ch-folder-toggle').evaluate(n=>n===document.activeElement),true);
  assert.equal(current(page).get('project'),null);
  assert.equal(await folder(page,pid).locator('.ch-folder-chat').count(),5);
  assert.equal(reads.filter(s=>s.includes(`/projects/${other}/conversations?limit=5`)).length,0);
  assert.equal(await folder(page,pid).locator('.sra-nav-folder').count(),1);
  assert.equal(await folder(page,pid).locator('.sra-nav-conversation').count(),5);
  assert.equal(reads.filter(s=>/\/messages(?:\?|$)/.test(s)).length,0);
  assert((await folder(page,pid).locator('.ch-project-disambiguator').count())>=2);
  await folder(page,other).locator('.ch-folder-toggle').click();await folder(page,other).locator('.ch-folder-chat').first().waitFor();
  assert.equal(await folder(page,other).locator('.ch-folder-chat').count(),3);
  for(const n of await folder(page,other).locator('.ch-folder-chat').all())assert.equal(await n.getAttribute('data-project-id'),other);
  await folder(page,pid).locator(`.ch-folder-chat[data-conversation-id="${first.conversation_id}"] .ch-conversation-link`).click();await ready(page);
  assert.equal(current(page).get('project'),pid);assert.equal(current(page).get('conversation'),first.conversation_id);
  await folder(page,pid).locator('.ch-folder-chat .ch-conversation-link[aria-current=page]').waitFor();
  assert.match(await folder(page,pid).innerText(),/当前项目/);assert.match(await folder(page,pid).innerText(),/当前对话/);
  const recent=page.locator(`#ch-conversations .ch-conversation-row[data-project-id="${pid}"][data-conversation-id="${first.conversation_id}"]`);
  await recent.waitFor();assert.match(await recent.locator('.ch-project-context').innerText(),/人工智能RAG/);
  await recent.locator('.ch-conversation-link').click();await ready(page);assert.equal(current(page).get('conversation'),first.conversation_id);
  // A draft and File belong to this exact project/conversation, including history.
  await page.locator('#ch-input').fill('项目 A 的未发文字，浏览与切换不可覆盖。');await chooseFile(page,path.join(initial.root,'selection.pdf'));
  await folder(page,pid).locator('.ch-project-link').click();await page.locator('.project-overview[data-loaded=true]').waitFor();
  assert.equal(current(page).get('page'),'project-overview');assert.equal(current(page).get('conversation'),first.conversation_id);
  await page.getByRole('button',{name:'返回刚才的对话',exact:true}).click();await ready(page);
  assert.equal(await page.locator('#ch-input').inputValue(),'项目 A 的未发文字，浏览与切换不可覆盖。');assert.match(await page.locator('[data-ref=filePending]').innerText(),/待提交资料/);
  await folder(page,other).locator(`.ch-folder-chat[data-conversation-id="${second.conversation_id}"] .ch-conversation-link`).click();await ready(page);
  assert.equal(await page.locator('#ch-input').inputValue(),'');assert.match(await page.locator('[data-ref=messages]').innerText(),new RegExp(other.slice(-6)));
  assert(!await page.locator('[data-ref=messages]').innerText().then(t=>t.includes(pid.slice(-6))));
  await page.goBack();await ready(page);assert.equal(current(page).get('conversation'),first.conversation_id);
  assert.match(await page.locator('[data-ref=filePending]').innerText(),/待提交资料/);
  await page.goForward();await ready(page);assert.equal(current(page).get('conversation'),second.conversation_id);
  await page.goBack();await ready(page);await page.reload();await ready(page);
  assert.equal(current(page).get('conversation'),first.conversation_id);assert.equal(await page.locator('#ch-input').inputValue(),'项目 A 的未发文字，浏览与切换不可覆盖。');
  await folder(page,pid).locator('.ch-conversation-link[aria-current=page]').waitFor();
  assert.match(await page.locator('[data-ref=filePending]').innerText(),/重新选择/);
  await page.locator('.ch-settings summary').click();assert.equal(await page.locator('[data-ref=belongs]').isDisabled(),true);assert.match(await page.locator('[data-ref=belongsNote]').innerText(),/不提供移动已有对话/);await page.locator('.ch-settings summary').click();
  // Late pagination remains confined to its own folder, even while collapsed.
  let entered;const got=new Promise(r=>entered=r),held=new Promise(r=>releaseHeld=r);
  await page.route(`**/api/projects/${pid}/conversations?limit=5&offset=5`,async r=>{const response=await r.fetch();entered();await held;await r.fulfill({response}).catch(()=>{});});
  await folder(page,pid).getByRole('button',{name:'加载更多项目对话',exact:true}).click();await got;
  await folder(page,pid).locator('.ch-folder-toggle').click();
  await folder(page,other).locator('.ch-project-link').click();await page.locator('.project-overview[data-loaded=true]').waitFor();
  releaseHeld();await page.unroute(`**/api/projects/${pid}/conversations?limit=5&offset=5`);
  assert.equal(await folder(page,other).locator('.ch-folder-chat').count(),3);
  await folder(page,pid).locator('.ch-folder-toggle').click();await page.waitForFunction(pid=>document.querySelectorAll(`.ch-folder[data-project-id="${pid}"] .ch-folder-chat`).length===8,pid);
  const ids=await folder(page,pid).locator('.ch-folder-chat').evaluateAll(nodes=>nodes.map(n=>n.dataset.conversationId));assert.equal(new Set(ids).size,8);
  // Error is not rendered as an empty folder; retry is an actual successful GET.
  let failing=true;await page.route(`**/api/projects/${blank}/conversations?limit=5&offset=0`,r=>failing?r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'离线注入读取失败'})}):r.continue());
  await folder(page,blank).locator('.ch-folder-toggle').click();await folder(page,blank).getByText(/项目对话读取失败/).waitFor();
  assert.equal(await folder(page,blank).getByText(/尚无对话/).count(),0);await page.screenshot({path:path.join(out,'folders-error-desktop.png'),fullPage:true});
  failing=false;await folder(page,blank).getByRole('button',{name:'重试读取项目对话',exact:true}).click();await folder(page,blank).getByText('尚无对话；明确发送后才保存。',{exact:true}).waitFor();await page.unroute(`**/api/projects/${blank}/conversations?limit=5&offset=0`);
  await page.screenshot({path:path.join(out,'folders-desktop.png'),fullPage:true});
  const searchResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/projects'&&new URL(r.url()).searchParams.get('q')==='没有匹配的项目');
  await page.locator('#ch-project-search').fill('没有匹配的项目');await searchResponse;await page.getByText('没有匹配项目',{exact:true}).waitFor();
  const clearSearch=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/projects'&&new URL(r.url()).searchParams.get('q')==='');await page.locator('#ch-project-search').fill('');await clearSearch;await folder(page,pid).waitFor();
  // All browsing up to this point preserved every project byte and call count.
  const browsed=await state(page);assert.deepEqual(browsed.snapshot,initial.snapshot);assert.deepEqual(browsed.calls,initial.calls);assert.deepEqual(writes,[]);
  fs.writeFileSync(path.join(out,'read-only-results.json'),JSON.stringify({passed:true,projectHashesUnchanged:true,metadataReadOnExpansion:true,noMessagesOnExpansion:true,paginationNoDuplicates:true,noWrites:true},null,2));
  // Every home entry is actionable without generating; opener clicks only prefill.
  await page.locator('#ch-new').click();await ready(page);
  for(let i=0;i<3;i++){await page.locator('#ch-input').fill('');await page.locator(`[data-entry="${i}"]`).click();assert((await page.locator('#ch-input').inputValue()).length>3);}
  await page.locator('#ch-input').fill('留在新研究的原草稿');await chooseFile(page,path.join(initial.root,'selection.pdf'),true);
  await page.locator('[data-home-action=projects]').click();await page.locator('.project-library').waitFor();
  await page.goBack();await ready(page);assert.equal(await page.locator('#ch-input').inputValue(),'留在新研究的原草稿');assert.match(await page.locator('[data-ref=filePending]').innerText(),/待提交资料/);
  await page.locator('.ch-settings summary').click();assert.equal(await page.locator('[data-ref=belongs]').isDisabled(),false);await page.locator('[data-ref=belongs]').selectOption(other);await ready(page);
  assert.equal(current(page).get('project'),other);assert.equal(current(page).get('conversation'),null);assert.equal(await page.locator('#ch-input').inputValue(),'');
  await page.goBack();await ready(page);assert.equal(await page.locator('#ch-input').inputValue(),'留在新研究的原草稿');assert.match(await page.locator('[data-ref=filePending]').innerText(),/待提交资料/);
  // Explicitly sending creates one independent conversation under the selected project.
  await page.goForward();await ready(page);await page.locator('#ch-input').fill('这是一段新的独立讨论，先解释怎样核对证据，不生成方案。');await page.locator('[data-ref=send]').click();
  await page.waitForFunction(()=>new URL(location.href).searchParams.get('conversation')&&document.querySelectorAll('.ch-message-assistant').length===1);await ready(page);
  const newCid=current(page).get('conversation');assert.equal(current(page).get('project'),other);
  assert.equal(await page.locator('.ch-message-user').count(),1);assert.equal(await page.locator('.ch-message-assistant').count(),1);
  const sent=await state(page);assert.equal(sent.snapshot[other].counts.sra_conversations,initial.snapshot[other].counts.sra_conversations+1);
  assert.equal(sent.calls.guidance_calls,initial.calls.guidance_calls+1);assert.equal(sent.calls.source_calls,initial.calls.source_calls);
  for(const id of Object.keys(initial.snapshot))if(id!==other)assert.deepEqual(sent.snapshot[id],initial.snapshot[id]);
  // Narrow viewport: home flow, folder operation, long accessible names, typing.
  await page.setViewportSize({width:390,height:844});await page.locator('#ch-menu').click();await page.locator('#ch-new').click();await ready(page);
  await page.screenshot({path:path.join(out,'home-390px.png'),fullPage:true});
  await page.locator('.ch-home-flow summary').click();assert.equal(await page.locator('.ch-home-steps li').count(),6);
  await page.locator('.ch-home-flow').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'home-flow-390px.png'),fullPage:true});
  await page.locator('#ch-menu').click();await folder(page,pid).locator('.ch-folder-toggle').focus();await page.keyboard.press('Space');
  if(await folder(page,pid).locator('.ch-folder-conversations').isHidden())await folder(page,pid).locator('.ch-folder-toggle').click();
  await folder(page,pid).locator('.ch-folder-chat').first().waitFor();
  const long=folder(page,pid).locator('.ch-conversation-link').filter({hasText:'这是一段需要完整辨认的长名称对话'});await long.focus();assert((await long.getAttribute('title')).length>70);
  await page.screenshot({path:path.join(out,'folders-390px.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await folder(page,pid).locator('.ch-project-link').click();await page.locator('.project-overview[data-loaded=true]').waitFor();assert.equal(await page.locator('.project-overview h1').innerText(),'人工智能RAG');
  // A separate B context cannot list or read A's project/conversation identifiers.
  const cb=await browser.newContext({viewport:{width:390,height:844}});await guard(cb);const pb=await cb.newPage();pb.on('pageerror',e=>errors.push(e.message));await login(pb,1);await pb.locator('#ch-menu').click();
  assert.equal(await folder(pb,pid).count(),0);assert.equal(await folder(pb,other).count(),0);
  assert.equal((await pb.request.get(`${base}/api/projects/${pid}/conversations?limit=5`)).status(),404);
  assert.equal((await pb.request.get(`${base}/api/projects/${pid}/conversations/${first.conversation_id}`)).status(),404);
  await cb.close();
  // Same-context logout/login clears folder caches; old A drafts never appear in B.
  await page.setViewportSize({width:1440,height:1000});await page.locator('#account-logout').click();await page.locator('#account-gate').waitFor({state:'visible'});await login(page,1);
  await page.locator('.ch-folder').first().waitFor();assert.equal(await folder(page,pid).count(),0);assert.equal(await page.locator('#ch-input').inputValue(),'');
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,readOnlyBrowsing:true,sameNameDisambiguation:true,lazyMetadata:true,pagination:true,errorRetry:true,lateResponseIsolation:true,routeHistory:true,draftsAndFiles:true,refreshReselection:true,newConversationId:newCid,newConversationProject:other,exactlyOneOfflineGeneration:true,dualAccount:true,desktop390Keyboard:true,homeEntries:true,errors,outside},null,2));
  console.log('PASS folders, independent conversations, homepage, scoped drafts, dual accounts, desktop/390px');
 }catch(e){await page.screenshot({path:path.join(out,`failure-${Date.now()}.png`),fullPage:true}).catch(()=>{});throw e;}finally{releaseHeld?.();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
