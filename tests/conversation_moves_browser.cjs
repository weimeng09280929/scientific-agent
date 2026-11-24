/* Real FastAPI routes, disposable accounts, no public network or model calls. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8098',out=path.resolve(process.env.SRA_MOVE_OUTPUT||'docs/conversation-moves');
const route=(pid,cid)=>base+`/?page=dashboard&project=${pid}&conversation=${cid}`;
const ready=p=>p.locator('[data-ref=send]:not(:disabled)').waitFor();
async function login(p,index){await p.goto(base);await p.locator('input[name=email]').fill(index?'backup-b@example.com':'backup-a@example.com');await p.locator('input[name=password]').fill(index?'Offline restore sample B!':'Offline restore sample A!');await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
async function open(p){await p.locator('.ch-title-menu>summary').click();await p.locator('[data-ref=move]').click();await p.locator('dialog[open]').waitFor();return p.locator('dialog[open]');}
async function choose(d,target){await d.locator(`select option[value="${target}"]`).waitFor({state:'attached'});await d.locator('select').selectOption(target);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await d.page().waitForFunction(()=>[...document.querySelectorAll('dialog[open] button')].some(b=>b.textContent==='预检查所选项目'&&!b.disabled));}
const state=async p=>(await p.request.get(base+'/__move__/state')).json();
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}}),p=await ctx.newPage();
 p.setDefaultTimeout(12000);const errors=[],outside=[],actions=[];
 async function guard(c){await c.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(new URL(r.request().url()).origin),r.abort()));}
 await guard(ctx);p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/move'))actions.push(r.postDataJSON().request_id);});
 let other;
 try{
  const initial=await state(p),s=initial.sample;await login(p,0);await p.goto(route(s.source,s.cid));await ready(p);
  // Both kinds of sidebar entry share the same move operation; precheck/cancel read only.
  const folder=p.locator(`.ch-folder[data-project-id="${s.source}"]`);await folder.locator('.ch-folder-toggle').click();await folder.locator(`[data-conversation-id="${s.cid}"]`).waitFor();
  const folderRow=folder.locator(`[data-conversation-id="${s.cid}"]`);await folderRow.locator('summary').click();await folderRow.getByRole('button',{name:'移动到项目',exact:true}).click();
  let d=p.locator('dialog[open]');await choose(d,s.target);await d.getByRole('button',{name:'确认移动',exact:true}).waitFor({state:'visible'});assert.equal(await d.locator('select option').filter({hasText:'同名项目'}).count(),1);
  await d.getByRole('button',{name:'取消',exact:true}).click();assert.deepEqual((await state(p)).snapshot,initial.snapshot);assert.equal(actions.length,0);
  // Draft/file selection blocks a move without discarding either.
  await p.locator('#ch-input').fill('整理前保留的未发送文字');await p.locator('[data-ref=import]').click();
  const [chooser]=await Promise.all([p.waitForEvent('filechooser'),p.getByRole('button',{name:'添加待上传论文',exact:true}).click()]);await chooser.setFiles(path.join(initial.root,'selection.pdf'));
  d=await open(p);assert.match(await d.innerText(),/先处理未发送/);assert.equal(await d.getByRole('button',{name:'确认移动',exact:true}).count(),0);await d.getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await p.locator('#ch-input').inputValue(),'整理前保留的未发送文字');assert.match(await p.locator('[data-ref=filePending]').innerText(),/selection.pdf/);
  await p.screenshot({path:path.join(out,'draft-protection-desktop.png'),fullPage:true});
  await p.locator('[data-ref=filePending]').getByRole('button',{name:/移除|取消/}).click();await p.locator('#ch-input').fill('');
  // An old tab remains open before the atomic move.
  const old=await ctx.newPage();await old.goto(route(s.source,s.cid));await ready(old);
  d=await open(p);await choose(d,s.target);await d.getByRole('button',{name:'确认移动',exact:true}).focus();await p.screenshot({path:path.join(out,'confirm-desktop.png'),fullPage:true});await d.getByRole('button',{name:'确认移动',exact:true}).click();
  await p.waitForURL(u=>u.searchParams.get('project')===s.target);await ready(p);assert.match(await p.locator('[data-ref=moveHistory]').innerText(),/同名项目/);assert.match(await p.locator('[data-ref=messages]').innerText(),/历史讨论 · 原项目/);
  const movedState=await state(p);assert.equal(movedState.snapshot[s.source].counts.sra_conversations,initial.snapshot[s.source].counts.sra_conversations-1);assert.equal(movedState.snapshot[s.target].counts.sra_conversations,1);assert.equal(movedState.calls.guidance_calls,0);
  await p.screenshot({path:path.join(out,'moved-desktop.png'),fullPage:true});
  await old.locator('#ch-input').fill('旧标签页中的新文字必须保留');await old.locator('[data-ref=send]').click();await old.getByRole('button',{name:'打开新的所属项目',exact:true}).waitFor();assert.equal(await old.locator('#ch-input').inputValue(),'旧标签页中的新文字必须保留');assert.equal((await state(p)).calls.guidance_calls,0);
  await old.reload();await old.getByRole('button',{name:'打开新的所属项目',exact:true}).waitFor();assert.equal(await old.locator('#ch-input').inputValue(),'旧标签页中的新文字必须保留');await old.close();
  await p.reload();await ready(p);assert.match(await p.locator('[data-ref=moveHistory]').innerText(),/移动到/);await p.goBack();await p.getByRole('button',{name:'打开新的所属项目',exact:true}).waitFor();await p.goForward();await ready(p);
  // A complex conversation is rejected from the recent menu, unchanged.
  await p.goto(route(s.source,s.complex));await ready(p);
  const recent=p.locator(`#ch-conversations [data-conversation-id="${s.complex}"]`);await recent.locator('summary').click();await recent.getByRole('button',{name:'移动到项目',exact:true}).click();d=p.locator('dialog[open]');await choose(d,s.target);await d.getByText(/含科研建议、草案/).waitFor();assert.equal(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled(),true);
  await p.screenshot({path:path.join(out,'blocked-complex-desktop.png'),fullPage:true});await d.getByRole('button',{name:'取消',exact:true}).click();
  // Version changes require a fresh explicit precheck, with the same move ID.
  await p.goto(route(s.source,s.stale));await ready(p);d=await open(p);await choose(d,s.target);
  await p.evaluate(async pid=>{const w=await (await fetch(`/api/projects/${pid}/workspace`)).json();const r=await fetch(`/api/projects/${pid}/workspace/brief`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:w.workspace.revision,brief:{resources:'目标项目条件已由另一标签页更新'}})});if(!r.ok)throw Error('fixture brief update failed');},s.target);
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/预检查后消息、材料或设置已变化/).waitFor();await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await d.getByRole('button',{name:'确认移动',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===s.target);await ready(p);assert.equal(actions.at(-1),actions.at(-2));
  // A committed move whose response is lost is recovered by querying the original ID.
  await p.goto(route(s.source,s.lost));await ready(p);let lost=true;
  await p.route('**/conversations/*/move',async r=>{if(lost){lost=false;await r.fetch();await r.abort('failed');}else await r.continue();});
  d=await open(p);await choose(d,s.third);await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/移动结果尚未确认/).waitFor();const beforeRecovery=actions.length;
  await d.getByRole('button',{name:'取消',exact:true}).click();await p.reload();await p.getByRole('button',{name:'打开新的所属项目',exact:true}).waitFor();
  // The retained request can be checked through the original dialog, even on a stale URL.
  d=await open(p);await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===s.third);await ready(p);assert.equal(actions.length,beforeRecovery);
  // Keyboard and 390px. No clipped select or horizontal page scrolling.
  await p.setViewportSize({width:390,height:844});d=await open(p);await choose(d,s.target);await d.getByRole('button',{name:'取消',exact:true}).focus();assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await p.screenshot({path:path.join(out,'confirm-390px.png'),fullPage:true});await p.keyboard.press('Escape');await d.waitFor({state:'detached'});
  // Independent account: no source/target, resource ID knowledge grants nothing.
  other=await browser.newContext({viewport:{width:390,height:844}});await guard(other);const b=await other.newPage();await login(b,1);
  const list=await b.evaluate(async()=>await(await fetch('/api/projects')).json());assert.deepEqual(list.projects.map(p=>p.project_id),[s.foreign]);const denied=await b.request.get(base+`/api/projects/${s.target}/conversations/${s.cid}/location`);assert.equal(denied.status(),404);
  const final=await state(p);assert.equal(final.calls.guidance_calls,0);assert.equal(final.calls.public_calls,0);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,journeys:9,realExternalCalls:0,modelCallsAfterSeed:0,seedOfflineCalls:s.seed_offline_calls,confirmRequestCount:actions.length,uniqueMoveRequests:new Set(actions).size,desktop:true,narrow390:true,errors,outside,sample:s},null,2));
 }catch(e){await p.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw e;}finally{await other?.close();await ctx.close();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
