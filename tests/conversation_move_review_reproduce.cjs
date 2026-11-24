/* Diagnostic only: prove the two reviewed defects against the unmodified UI. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8098',out=path.resolve('docs/conversation-move-review/reproduction');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function open(p){await p.locator('.ch-title-menu>summary').click();await p.locator('[data-ref=move]').click();return p.locator('dialog[open]');}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}}),p=await context.newPage();p.setDefaultTimeout(15000);
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 try{
  const initial=await(await p.request.get(base+'/__move__/state')).json(),s=initial.sample;
  await p.goto(base);await p.locator('input[name=email]').fill('backup-a@example.com');await p.locator('input[name=password]').fill('Offline restore sample A!');await p.getByRole('button',{name:'登录',exact:true}).click();
  await p.locator('[data-ref=send]:not(:disabled)').waitFor();await p.goto(base+`/?page=dashboard&project=${s.source}&conversation=${s.cid}`);await p.locator('[data-ref=send]:not(:disabled)').waitFor();
  const received=deferred(),release=deferred();let aToken;
  await p.route('**/move-precheck?*',async r=>{const response=await r.fetch();aToken=(await response.json()).token;received.resolve();await release.promise;await r.fulfill({response});});
  let d=await open(p);await d.locator(`option[value="${s.target}"]`).waitFor({state:'attached'});await d.locator('select').selectOption(s.target);await d.getByRole('button',{name:'预检查所选项目',exact:true}).click();await received.promise;
  await d.locator('select').selectOption(s.third);release.resolve();await d.getByText('预检查通过。请核对目标项目与历史来源说明，然后明确确认。',{exact:true}).waitFor();assert.equal(await d.getByRole('button',{name:'确认移动',exact:true}).isEnabled(),true);
  await p.screenshot({path:path.join(out,'wrong-target-authorized.png'),fullPage:true});
  const sent=p.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/move'));await d.getByRole('button',{name:'确认移动',exact:true}).click();const body=(await sent).postDataJSON();assert.equal(body.target_project_id,s.third);assert.equal(body.expected_token,aToken);
  await d.getByText(/预检查后消息、材料或设置已变化/).waitFor();await d.getByRole('button',{name:'取消',exact:true}).click();d=await open(p);assert.equal(await d.locator('select').isDisabled(),true);assert.equal(await d.getByRole('button',{name:'重新选择目标',exact:true}).count(),0);
  await p.screenshot({path:path.join(out,'rejected-target-locked.png'),fullPage:true});await d.getByRole('button',{name:'取消',exact:true}).click();await p.reload();await p.locator('[data-ref=send]:not(:disabled)').waitFor();d=await open(p);assert.equal(await d.locator('select').isDisabled(),true);
  const final=await(await p.request.get(base+'/__move__/state')).json();assert.deepEqual(final.snapshot,initial.snapshot);assert.equal(final.calls.guidance_calls,0);
  fs.writeFileSync(path.join(out,'diagnostic.json'),JSON.stringify({diagnosticOnly:true,defectReproduced:true,lateATokenAuthorizedB:true,serverRejectedMismatch:true,explicitRejectionLocksOnReopenAndRefresh:true,noModelCalls:true,noBusinessWrites:true},null,2));
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
