/* Saved plan page and central conversation use the same real read-only reader. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const base=process.env.SRA_PLAN_TEST_URL||'http://127.0.0.1:8058',shots=path.resolve('docs/ui-experience-unification/reading');
(async()=>{
 fs.mkdirSync(shots,{recursive:true});const b=await chromium.launch({channel:'msedge',headless:true});const p=await b.newPage({viewport:{width:1440,height:900}}),errors=[],writes=[];
 p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
 try{
  const fixture=await(await p.request.get(base+'/__fixture__/state')).json(),pid=fixture.project_id;
  const shared=await(await p.request.get(base+`/api/projects/${pid}/workspace?include_chat=false`)).json(),plan=shared.workflow.flow.plan;
  await p.goto(base+`/workspace?page=experiments&project=${pid}`);
  const entry=p.locator(`[data-record-id="${plan.item_id}"]`);await entry.waitFor();await entry.locator('> summary').click();
  await entry.getByRole('button',{name:'打开完整方案',exact:true}).click();
  const reader=p.locator(`.ch-plan-review[data-plan-id="${plan.item_id}"]`).first();await reader.waitFor({state:'visible'});
  await p.locator('[data-ref="panelWide"]').click();assert.equal(await p.locator('.ch-center').isVisible(),false);
  const rect=await p.locator('[data-ref="panel"]').boundingBox();assert(rect.width>700);
  await p.screenshot({path:path.join(shots,'wide-desktop.png')});
  await p.locator('[data-ref="panel"]').evaluate(n=>n.scrollTop=n.scrollHeight);
  const link=reader.getByRole('link',{name:'下载此版本（MD）',exact:true});await link.waitFor();assert(await link.isVisible());
  await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await p.locator('[data-ref="panel"]').evaluate(n=>n.scrollTop=0);await p.locator('[data-ref="panelClose"]').focus();
  await p.keyboard.press('Tab');assert(await p.locator('[data-ref="panel"]').evaluate(n=>n.contains(document.activeElement)));
  await p.screenshot({path:path.join(shots,'reader-mobile.png')});
  await p.keyboard.press('Escape');assert.equal(await p.locator('[data-ref="panel"]').isVisible(),false);
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,savedPlanPageToSharedReader:true,wideReading:true,mobile:true,keyboard:true,noWrites:true,errors,screenshots:shots}));
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1);});
