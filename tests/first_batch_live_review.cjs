/* Review saved REAL replies only. No new generation/search requests. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8064',out=path.resolve('docs/first-batch-reliability-fixes'),data=JSON.parse(fs.readFileSync(path.join(out,'live-results.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let forbidden=0;
 await context.route(/\/conversations\/[^/]+\/messages$/,route=>{if(route.request().method()==='POST'){forbidden++;return route.abort();}return route.continue();});
 try{
  const route=async(name)=>{await page.goto(`${base}/workspace?page=dashboard&project=${data.project_id}&conversation=${data.scenarios[name].conversation_id}`);await page.locator('.ch-message-assistant').waitFor();};
  const shot=async(name)=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,name+'.png')});};
  await route('beginner');assert.equal(await page.locator('.ch-message-user').count(),1);await shot('real-guidance-desktop');
  await page.setViewportSize({width:390,height:844});await shot('real-guidance-mobile');
  await page.setViewportSize({width:1440,height:900});await route('question');assert.equal(await page.locator('.ch-message-user').count(),1);await shot('real-question-desktop');
  await route('plan');await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
  const review=page.locator('.ch-material-panel .ch-plan-review[open]').last();await review.waitFor();assert.match(await review.innerText(),/未采纳讨论稿/);assert.match(await review.innerText(),/待确认/);await shot('real-plan-draft-desktop');
  await page.setViewportSize({width:390,height:844});await shot('real-plan-draft-mobile');
  const downloadP=page.waitForEvent('download');await review.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();const download=await downloadP,body=fs.readFileSync(await download.path(),'utf8');
  assert.match(body,/未采纳讨论稿/);assert.match(body,/普通笔记本 CPU/);assert.match(body,/四周/);assert.match(body,/待确认/);
  fs.writeFileSync(path.join(out,'real-draft-export.md'),body);
  // Explicit tester action, not interpreting the model's text as adoption.
  await page.setViewportSize({width:1440,height:900});await review.getByRole('button',{name:'采纳方案',exact:true}).click();
  await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
  const workspace=await(await page.request.get(`${base}/api/projects/${data.project_id}/workspace?include_chat=false`)).json();assert.equal(workspace.workflow.flow.planReady,true);assert.equal(workspace.workflow.flow.plan.plan.conditions.budget.value,'待确认');
  await shot('real-plan-adopted-desktop');await page.reload();await page.locator('.ch-message-assistant').waitFor();assert.equal(await page.locator('.ch-message-user').count(),1);
  assert.equal(forbidden,0);assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'real-browser-results.json'),JSON.stringify({passed:true,model_or_search_posts:forbidden,errors,draft_export_bytes:Buffer.byteLength(body),explicit_tester_adoption:true,planReady:workspace.workflow.flow.planReady},null,2));
  console.log('PASS: three real replies readable; draft review/export; explicit UI adoption; refresh; desktop/mobile; no extra model requests');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
