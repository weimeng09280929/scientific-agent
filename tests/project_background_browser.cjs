/* Real browser/API/SQLite. Provider outputs are explicitly offline fixtures. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8098',out=path.resolve('docs/project-background');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const dialog=p=>p.locator('dialog.project-background-editor');
const edit=async p=>{await p.getByRole('button',{name:'编辑项目背景',exact:true}).first().click();await dialog(p).getByRole('button',{name:'预览本次修改',exact:true}).waitFor();await dialog(p).locator('textarea[name=goal]:not([readonly])').waitFor();};
const field=(p,k)=>dialog(p).locator(`textarea[name=${k}]`);
const btn=(p,n)=>dialog(p).getByRole('button',{name:n,exact:true});
const tick=p=>p.evaluate(()=>new Promise(requestAnimationFrame));
const ready=p=>p.locator('[data-ref=send]:not(:disabled)').waitFor();
async function overview(p,s){await p.goto(base+`/?page=project-overview&project=${s.pid}&conversation=${s.cid}`);await p.locator('.project-overview[data-loaded=true]').waitFor();}
async function chat(p,s){await p.goto(base+`/?page=dashboard&project=${s.pid}&conversation=${s.cid}`);await ready(p);}
async function previewSave(p){await btn(p,'预览本次修改').click();await btn(p,'确认保存项目背景').click();await btn(p,'完成').waitFor();}
async function login(p,index=0){await p.goto(base);await p.locator('input[name=email]').fill(`backup-${index?'b':'a'}@example.com`);await p.locator('input[name=password]').fill(`Offline restore sample ${index?'B':'A'}!`);await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});const p=await context.newPage();p.setDefaultTimeout(15000);
 const errors=[],outside=[],cases=[];let puts=0;
 context.on('page',x=>x.on('pageerror',e=>errors.push(e.message)));p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(new URL(r.request().url()).origin),r.abort()));
 context.on('request',r=>{if(r.method()==='PUT'&&r.url().endsWith('/workspace/brief'))puts++;});
 const state=async()=>await(await p.request.get(base+'/__background__/state')).json();
 const work=async(pid,pg=p)=>pg.evaluate(async pid=>(await(await fetch(`/api/projects/${pid}/workspace?include_chat=false`)).json()).workspace,pid);
 try{
  const initial=await state(),s=initial.sample;await login(p);
  await overview(p,s.empty);assert.equal(await p.locator('[data-section=background] dd').count(),4);assert.equal(await p.getByText('尚未明确',{exact:true}).count(),4);
  const empty=await work(s.empty.pid);await edit(p);await field(p,'goal').fill('取消不能保存');await btn(p,'取消编辑').click();assert.deepEqual(await work(s.empty.pid),empty);assert.equal(puts,0);cases.push('empty fields, cancel and browsing have no writes');
  await overview(p,s.full);const before=await work(s.full.pid);await edit(p);assert.equal(await field(p,'resources').inputValue(),before.brief.resources);
  await field(p,'background').fill('已学 Python，正在比较检索方法');await field(p,'time_budget').fill('');await btn(p,'预览本次修改').click();
  assert.match(await dialog(p).innerText(),/清空（尚未明确）/);assert.equal(puts,0);await p.screenshot({path:path.join(out,'preview-desktop.png'),fullPage:true});
  await btn(p,'确认保存项目背景').click();await btn(p,'完成').waitFor();assert.match(await dialog(p).innerText(),/1 个有效研究问题、1 份正式方案待复核/);
  const first=await work(s.full.pid);assert.deepEqual(first.brief,{...before.brief,background:'已学 Python，正在比较检索方法',time_budget:''});assert(first.items.filter(i=>['question','plan'].includes(i.kind)).every(i=>i.needs_review));assert.equal(puts,1);
  await btn(p,'完成').click();await p.locator('.project-overview[data-loaded=true]').waitFor();assert.equal(await p.locator('[data-section=background] [data-background-field=time_budget]').innerText(),'尚未明确');await p.reload();await p.locator('.project-overview[data-loaded=true]').waitFor();assert.deepEqual(await work(s.full.pid),first);cases.push('preview, explicit save, clear, untouched fields, needs-review and refresh');
  await chat(p,s.full);await p.locator('#ch-input').fill('未发送的原对话输入');await p.locator('[data-ref=import]').click();const chooser=p.waitForEvent('filechooser');await p.getByRole('button',{name:'添加待上传论文',exact:true}).click();await(await chooser).setFiles(path.join(initial.root,'selection.pdf'));
  await p.locator('[data-ref=filePending]').filter({hasText:'selection.pdf'}).waitFor();await p.locator('.ch-background-scope summary').click();await p.getByRole('button',{name:'查看项目背景',exact:true}).click();await p.locator('[data-material-kind=background]').waitFor();
  await edit(p);await field(p,'goal').fill('比较检索依据，明确适用条件');await previewSave(p);await btn(p,'完成').click();
  assert.equal(await p.locator('#ch-input').inputValue(),'未发送的原对话输入');assert.match(await p.locator('[data-ref=filePending]').innerText(),/selection.pdf/);
  await p.screenshot({path:path.join(out,'shared-material-editor-desktop.png'),fullPage:true});cases.push('conversation material uses same editor without losing unsent text/file');
  // A new conversation uses only the current shared background, no other transcript.
  const newChat=await p.evaluate(async pid=>(await(await fetch(`/api/projects/${pid}/conversations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'跨对话背景验收'})})).json()).conversation,s.full.pid);
  await chat(p,{pid:s.full.pid,cid:newChat.conversation_id});await p.locator('#ch-input').fill('只解释当前背景，不保存新条件，不检索。');await p.locator('[data-ref=send]').click();await p.locator('.ch-message-assistant').waitFor();await ready(p);
  let trace=(await state()).calls.at(-1);assert.deepEqual(trace.context.brief,(await work(s.full.pid)).brief);assert.deepEqual(trace.context.history,[]);assert.equal(trace.context.context_scope.history_messages,0);assert.match(trace.context.project_background.notice,/不从旧聊天补回/);cases.push('actual new-chat generator context includes current Brief without other transcripts');
  // Independent tabs edit the same revision. Conflict must retain only edited keys.
  await overview(p,s.full);const q=await context.newPage();q.setDefaultTimeout(15000);await overview(q,s.full);await edit(p);await edit(q);
  await field(p,'background').fill('标签页 A 新的基础');await field(q,'time_budget').fill('八周');const pBefore=puts;await previewSave(p);await btn(p,'完成').click();
  await btn(q,'预览本次修改').click();await btn(q,'确认保存项目背景').click();await btn(q,'我已核对，保留我的修改').waitFor();assert.equal(await field(q,'time_budget').inputValue(),'八周');assert.match(await dialog(q).innerText(),/标签页 A 新的基础/);assert.equal(puts,pBefore+2);
  await q.screenshot({path:path.join(out,'conflict-desktop.png'),fullPage:true});await btn(q,'我已核对，保留我的修改').click();assert.equal(await field(q,'background').inputValue(),'标签页 A 新的基础');assert.equal(puts,pBefore+2);await previewSave(q);await btn(q,'完成').click();assert.equal((await work(s.full.pid)).brief.time_budget,'八周');await q.close();cases.push('two tabs conflict, retain input, show latest, explicit rebase merges untouched fields');
  // A committed response is lost. Reopen/refresh reads before any further write.
  await overview(p,s.full);await edit(p);await field(p,'goal').fill('响应丢失后的目标');const committed=deferred();
  await p.route('**/workspace/brief',async r=>{const response=await r.fetch();assert.equal(response.status(),200);committed.resolve();await r.abort('failed');},{times:1});
  const lostBefore=puts;await btn(p,'预览本次修改').click();await btn(p,'确认保存项目背景').click();await committed.promise;await btn(p,'核对保存结果').waitFor();
  await btn(p,'关闭窗口，稍后核对').click();await p.reload();await p.locator('.project-overview[data-loaded=true]').waitFor();await p.getByRole('button',{name:'编辑项目背景',exact:true}).click();await btn(p,'完成').waitFor();assert.match(await dialog(p).innerText(),/不会重复保存/);assert.equal(puts,lostBefore+1);await btn(p,'完成').click();cases.push('lost successful response persists pending; reload verifies result without another PUT');
  // The first PUT has not reached the server, and a GET at the old revision
  // cannot prove it will never commit. Only an identical original CAS is allowed.
  await edit(p);await field(p,'resources').fill('只有 CPU；不假定其他设备');const held=deferred(),release=deferred();let originalBody;
  await p.route('**/workspace/brief',async r=>{originalBody=r.request().postDataJSON();held.resolve();await release.promise;await r.continue();},{times:1});
  await btn(p,'预览本次修改').click();await btn(p,'确认保存项目背景').click();await held.promise;await btn(p,'关闭窗口，稍后核对').click();
  await p.getByRole('button',{name:'编辑项目背景',exact:true}).click();await btn(p,'用原版本重新提交本次修改').waitFor();assert.match(await dialog(p).innerText(),/原提交仍可能完成/);assert.equal(await field(p,'resources').getAttribute('readonly'),'');
  const identical=p.waitForRequest(r=>r.method()==='PUT'&&r.url().endsWith('/workspace/brief'));await btn(p,'用原版本重新提交本次修改').click();assert.deepEqual((await identical).postDataJSON(),originalBody);await btn(p,'完成').waitFor();
  const finishOriginal=p.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/workspace/brief'));release.resolve();assert.equal((await finishOriginal).status(),409);await tick(p);assert.equal((await work(s.full.pid)).revision,originalBody.expected_revision+1);await btn(p,'完成').click();cases.push('unknown old-revision read stays pending; explicit identical CAS commits at most once');
  // Read failure shows a failure with retry, never fake empty confirmed fields.
  await p.route('**/workspace?include_chat=false',r=>r.fulfill({status:503,json:{detail:'离线读取故障'}}),{times:1});await p.getByRole('button',{name:'编辑项目背景',exact:true}).click();await btn(p,'重新读取项目背景').waitFor();assert.equal(await dialog(p).locator('form:visible').count(),0);assert.match(await dialog(p).innerText(),/未用空数据代替/);await btn(p,'重新读取项目背景').click();await p.locator('dialog textarea[name=goal]:not([readonly])').waitFor();assert.equal(await field(p,'goal').inputValue(),'响应丢失后的目标');await btn(p,'取消编辑').click();cases.push('editor read failure is explicit and retry retains truthful data');
  // A late read after closing must not populate a reopened different project.
  const readHeld=deferred(),readRelease=deferred();await p.route('**/workspace?include_chat=false',async r=>{const response=await r.fetch();readHeld.resolve();await readRelease.promise;await r.fulfill({response}).catch(()=>{});},{times:1});
  await p.getByRole('button',{name:'编辑项目背景',exact:true}).click();await readHeld.promise;await btn(p,'关闭窗口，稍后核对').click();await overview(p,s.other);await edit(p);assert.equal(await field(p,'goal').inputValue(),'');readRelease.resolve();await tick(p);assert.equal(await field(p,'goal').inputValue(),'');await field(p,'goal').fill('另一项目的专用目标');await previewSave(p);await btn(p,'完成').click();assert.equal((await work(s.full.pid)).brief.goal,'响应丢失后的目标');cases.push('closed late read cannot populate another project editor');
  // 390px and keyboard-only opening/cancel: native dialog remains navigable.
  await p.setViewportSize({width:390,height:844});await overview(p,s.full);const opener=p.getByRole('button',{name:'编辑项目背景',exact:true});await opener.focus();await p.keyboard.press('Enter');await p.locator('dialog textarea[name=goal]:not([readonly])').waitFor();await field(p,'goal').focus();await field(p,'goal').fill('窄屏键盘编辑，尚未保存');await btn(p,'预览本次修改').focus();await p.keyboard.press('Enter');await p.screenshot({path:path.join(out,'preview-390px.png'),fullPage:true});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await btn(p,'确认保存项目背景').focus();await p.screenshot({path:path.join(out,'confirm-controls-390px.png')});assert(await btn(p,'确认保存项目背景').isVisible());await p.keyboard.press('Escape');assert.equal(await dialog(p).count(),0);assert.equal((await work(s.full.pid)).brief.goal,'响应丢失后的目标');cases.push('390px readable diff and keyboard Enter/Escape preserve confirmed background');
  // Account B gets its own background and cannot open A, including known IDs.
  await p.setViewportSize({width:1440,height:1000});const accountHeld=deferred(),accountRelease=deferred();await p.route('**/workspace?include_chat=false',async r=>{const response=await r.fetch();accountHeld.resolve();await accountRelease.promise;await r.fulfill({response}).catch(()=>{});},{times:1});
  await p.getByRole('button',{name:'编辑项目背景',exact:true}).click();await accountHeld.promise;await btn(p,'关闭窗口，稍后核对').click();await p.getByRole('button',{name:'退出',exact:true}).click();await p.locator('input[name=email]').waitFor();await login(p,1);await overview(p,s.foreign);await edit(p);assert.equal(await field(p,'goal').inputValue(),'');accountRelease.resolve();await tick(p);assert.equal(await field(p,'goal').inputValue(),'');await btn(p,'取消编辑').click();assert.equal((await p.request.get(base+`/api/projects/${s.full.pid}/workspace`)).status(),404);await p.screenshot({path:path.join(out,'account-b-desktop.png'),fullPage:true});cases.push('account switch with an A late read cannot populate B editor or expose A resources');
  const final=await state();assert.equal(final.state.public_calls,0);assert.equal(final.state.source_calls,0);assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  fs.writeFileSync(path.join(out,'actual-contexts.json'),JSON.stringify(final.calls,null,2));fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,cases,casesCount:cases.length,puts,providerSpyCalls:final.calls.length,seedOfflineCalls:final.seed_offline_calls,realExternalCalls:0,errors,outside,root:initial.root},null,2));
 }catch(e){await p.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw e;}finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});

