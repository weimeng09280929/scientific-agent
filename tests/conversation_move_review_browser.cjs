/* Controlled response ordering over real FastAPI routes and disposable SQLite. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8098',out=path.resolve(process.env.SRA_MOVE_REVIEW_OUTPUT||'docs/conversation-move-review/final');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const ready=p=>p.locator('[data-ref=send]:not(:disabled)').waitFor();
const url=(s,cid)=>base+`/?page=dashboard&project=${s.source}&conversation=${cid}`;
async function open(p){await p.locator('.ch-title-menu>summary').click();await p.locator('[data-ref=move]').click();await p.locator('dialog[open]').waitFor();return p.locator('dialog[open]');}
async function select(d,pid){await d.locator(`option[value="${pid}"]`).waitFor({state:'attached'});await d.locator('select').selectOption(pid);}
async function pre(d,pid){await select(d,pid);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await d.getByText('预检查通过。请核对目标项目与历史来源说明，然后明确确认。',{exact:true}).waitFor();assert(await d.getByRole('button',{name:'确认移动',exact:true}).isEnabled());}
async function login(p,i=0){await p.goto(base);await p.locator('input[name=email]').fill(i?'backup-b@example.com':'backup-a@example.com');await p.locator('input[name=password]').fill(i?'Offline restore sample B!':'Offline restore sample A!');await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
async function change(p,pid,label){await p.evaluate(async({pid,label})=>{const w=await(await fetch(`/api/projects/${pid}/workspace`)).json();const r=await fetch(`/api/projects/${pid}/workspace/brief`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:w.workspace.revision,brief:{resources:label}})});if(!r.ok)throw Error('Temporary condition update failed');},{pid,label});}
async function submit(d,p,pid){await d.getByRole('button',{name:'确认移动',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===pid);await ready(p);}
async function cancel(d){await d.getByRole('button',{name:/^(取消|关闭窗口)$/}).click();}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}}),p=await context.newPage();
 p.setDefaultTimeout(15000);const outside=[],errors=[],posts=[],cases=[],handlers=[];let accountB;
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(new URL(r.request().url()).origin),r.abort()));
 p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/move'))posts.push(r.postDataJSON());});
 const state=async()=>await(await p.request.get(base+'/__move__/state')).json();
 async function hold(pattern,before=false){
  const arrived=deferred(),release=deferred(),finished=deferred();let response,body;
  const fn=async r=>{body=r.request().postDataJSON();try{if(!before)response=await r.fetch();arrived.resolve();await release.promise;if(before)response=await r.fetch();try{await r.fulfill({response});}catch(_){}finished.resolve();}catch(e){arrived.resolve();finished.resolve();throw e;}};
  await p.route(pattern,fn);const h={arrived:arrived.promise,release:release.resolve,finished:finished.promise,response:()=>response,body:()=>body,remove:()=>p.unroute(pattern,fn)};handlers.push(h);return h;
 }
 try{
  const initial=await state(),s=initial.sample;await login(p);await p.goto(url(s,s.cid));await ready(p);
  // 1. A finishes only after a deliberate target change to B.
  let d=await open(p),a=await hold('**/move-precheck?target_project_id='+s.target);
  await select(d,s.target);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await a.arrived;
  await select(d,s.third);assert(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled());assert(!/预检查通过/.test(await d.innerText()));a.release();await a.finished;
  assert.equal(await d.locator('select').inputValue(),s.third);assert(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled());assert(!/预检查通过/.test(await d.innerText()));await a.remove();await cancel(d);cases.push('A late result cannot authorize B');
  // 2. B returns before A; only B's actual server token can be submitted.
  d=await open(p);a=await hold('**/move-precheck?target_project_id='+s.target);const b=await hold('**/move-precheck?target_project_id='+s.third);
  await select(d,s.target);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await a.arrived;await select(d,s.third);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await b.arrived;
  const bToken=(await b.response().json()).token;b.release();await b.finished;await d.getByText(/^预检查通过/).waitFor();a.release();await a.finished;assert.equal(await d.locator('select').inputValue(),s.third);
  await p.screenshot({path:path.join(out,'current-target-desktop.png'),fullPage:true});await a.remove();await b.remove();await submit(d,p,s.third);assert.equal(posts.at(-1).expected_token,bToken);assert.equal(posts.at(-1).target_project_id,s.third);cases.push('B before A uses B token, one actual move');
  // 3. A result from a closed window cannot authorize the newly opened one.
  await p.goto(url(s,s.review_refresh));await ready(p);d=await open(p);a=await hold('**/move-precheck?target_project_id='+s.target);
  await select(d,s.target);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await a.arrived;await cancel(d);d=await open(p);await select(d,s.third);a.release();await a.finished;
  assert(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled());assert(!/预检查通过/.test(await d.innerText()));assert.equal(new URL(p.url()).searchParams.get('project'),s.source);await a.remove();await cancel(d);cases.push('closed precheck cannot affect reopened window');
  // 4. A definite refusal provides a safe explicit exit, followed by a new target.
  await p.goto(url(s,s.stale));await ready(p);d=await open(p);await pre(d,s.target);await change(p,s.target,'复核：明确拒绝后重新选择');
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/本次提交已明确拒绝且未执行/).waitFor();const rejectedRid=posts.at(-1).request_id;
  await p.screenshot({path:path.join(out,'rejected-reselect-desktop.png'),fullPage:true});await d.getByRole('button',{name:'重新选择目标',exact:true}).click();assert.equal(await d.locator('select').isEnabled(),true);assert.equal(await d.locator('select').inputValue(),'');assert(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled());
  await pre(d,s.third);await submit(d,p,s.third);assert.notEqual(posts.at(-1).request_id,rejectedRid);cases.push('explicit refusal -> user reselect -> new target succeeds');
  // 5. Refusal remains safely escapable through close/reopen AND refresh.
  await p.goto(url(s,s.review_refresh));await ready(p);d=await open(p);await pre(d,s.target);await change(p,s.target,'复核：刷新后仍可退出拒绝');await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByRole('button',{name:'重新选择目标',exact:true}).waitFor();
  await cancel(d);d=await open(p);assert(await d.getByRole('button',{name:'重新选择目标',exact:true}).isVisible());await cancel(d);await p.reload();await ready(p);d=await open(p);assert(await d.getByRole('button',{name:'重新选择目标',exact:true}).isVisible());
  await p.setViewportSize({width:390,height:844});await p.screenshot({path:path.join(out,'rejected-reselect-390px.png'),fullPage:true});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await d.getByRole('button',{name:'重新选择目标',exact:true}).focus();await p.keyboard.press('Enter');assert(await d.locator('select').isEnabled());await pre(d,s.third);await submit(d,p,s.third);await p.setViewportSize({width:1440,height:1000});cases.push('rejection reopen/refresh and keyboard reselect');
  // 6. Lost committed response is queried by its original ID, without a second POST.
  await p.goto(url(s,s.lost));await ready(p);d=await open(p);await pre(d,s.target);let loss=true;
  const lose=async r=>{const response=await r.fetch();if(loss){loss=false;await r.abort('failed');}else await r.fulfill({response});};await p.route('**/conversations/'+s.lost+'/move',lose);
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/移动结果尚未确认/).waitFor();const count=posts.length;await cancel(d);await p.reload();await p.getByRole('button',{name:'打开新的所属项目',exact:true}).waitFor();d=await open(p);
  await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===s.target);await ready(p);assert.equal(posts.length,count);await p.unroute('**/conversations/'+s.lost+'/move',lose);cases.push('lost committed response -> original receipt, no repost');
  // 7. The POST is delayed BEFORE the server sees it: a real 404 cannot unlock.
  await p.goto(url(s,s.review_inflight));await ready(p);d=await open(p);await pre(d,s.target);let held=await hold('**/conversations/'+s.review_inflight+'/move',true);
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await held.arrived;await cancel(d);d=await open(p);await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await d.getByText(/原提交仍可能执行/).waitFor();
  assert(await d.locator('select').isDisabled());assert.equal(await d.getByRole('button',{name:'重新选择目标',exact:true}).isVisible(),false);assert(await d.getByRole('button',{name:'确认移动',exact:true}).isDisabled());const delayedRid=held.body().request_id;
  await p.screenshot({path:path.join(out,'unknown-404-kept-desktop.png'),fullPage:true});held.release();await held.finished;await held.remove();
  assert.equal(new URL(p.url()).searchParams.get('project'),s.source);assert(await d.isVisible());await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===s.target);await ready(p);assert.equal(posts.filter(v=>v.request_id===delayedRid).length,1);cases.push('404 while original can execute stays locked; closed completion does not navigate');
  // 7b. An old refusal token is not proof about a fresh explicitly reviewed attempt.
  await p.goto(url(s,s.review_proof));await ready(p);d=await open(p);await pre(d,s.target);await change(p,s.target,'复核：旧拒绝回执不能覆盖新提交');await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByRole('button',{name:'重新选择目标',exact:true}).waitFor();const oldPost=posts.at(-1);
  await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await d.getByText(/^预检查通过/).waitFor();held=await hold('**/conversations/'+s.review_proof+'/move',true);await d.getByRole('button',{name:'确认移动',exact:true}).click();await held.arrived;assert.equal(held.body().request_id,oldPost.request_id);assert.notEqual(held.body().expected_token,oldPost.expected_token);
  await cancel(d);d=await open(p);await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await d.getByText(/尚无与本次提交完全对应/).waitFor();assert(await d.locator('select').isDisabled());assert.equal(await d.getByRole('button',{name:'重新选择目标',exact:true}).isVisible(),false);
  held.release();await held.finished;await held.remove();await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await p.waitForURL(u=>u.searchParams.get('project')===s.target);await ready(p);assert.equal(posts.filter(v=>v.request_id===oldPost.request_id).length,2);cases.push('version retry keeps ID; old negative receipt cannot unlock a new attempt');
  // A 409 meaning already moved is different from a refusal of this request.
  await p.goto(url(s,s.review_relocated));await ready(p);d=await open(p);await pre(d,s.target);
  await p.evaluate(async({source,cid,target})=>{const prefix=`/api/projects/${source}/conversations/${cid}`;const pre=await(await fetch(prefix+'/move-precheck?target_project_id='+target)).json();const r=await fetch(prefix+'/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_project_id:target,expected_token:pre.token,request_id:'other-explicit-tab',acknowledge_history:true})});if(!r.ok)throw Error('Other tab move failed');},{source:s.source,cid:s.review_relocated,target:s.third});
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/不等于本次未执行/).waitFor();assert.equal(await d.getByRole('button',{name:'重新选择目标',exact:true}).isVisible(),false);assert(await d.locator('select').isDisabled());await cancel(d);cases.push('409 relocated is not a non-execution authorization');
  // Lost rejection is recovered from the persisted negative receipt, not a 404 guess.
  await p.goto(url(s,s.review_refusal_lost));await ready(p);d=await open(p);await pre(d,s.target);await change(p,s.target,'复核：拒绝回执丢失');
  const rejectLoss=async r=>{const response=await r.fetch();assert.equal(response.status(),409);await r.abort('failed');};await p.route('**/conversations/'+s.review_refusal_lost+'/move',rejectLoss);
  await d.getByRole('button',{name:'确认移动',exact:true}).click();await d.getByText(/移动结果尚未确认/).waitFor();await p.unroute('**/conversations/'+s.review_refusal_lost+'/move',rejectLoss);await cancel(d);d=await open(p);await d.getByRole('button',{name:'核对原移动结果',exact:true}).click();await d.getByRole('button',{name:'重新选择目标',exact:true}).waitFor();await d.getByRole('button',{name:'重新选择目标',exact:true}).click();await pre(d,s.third);await submit(d,p,s.third);cases.push('lost refusal recovered by exact persistent proof, then explicit new target');
  const final=await state();assert.equal(final.calls.guidance_calls,0);assert.equal(final.calls.public_calls,0);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  accountB=await browser.newContext();await accountB.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(new URL(r.request().url()).origin),r.abort()));const q=await accountB.newPage();await login(q,1);const denied=await q.request.get(base+`/api/projects/${s.source}/conversations/${s.review_inflight}/move-requests/${delayedRid}`);assert.equal(denied.status(),404);cases.push('other account cannot query refusal or completion');
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,cases,casesCount:cases.length,realExternalCalls:0,modelCallsDuringJourneys:0,seedOfflineCalls:s.seed_offline_calls,postCount:posts.length,distinctRequestIds:new Set(posts.map(v=>v.request_id)).size,root:initial.root,sample:s,errors,outside},null,2));
 }catch(e){await p.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw e;}
 finally{for(const h of handlers)h.release();await accountB?.close();await context.close();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
