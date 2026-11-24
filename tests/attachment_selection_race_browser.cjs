/* Real FastAPI/main app. Only actual HTTP response delivery is delayed.
   Never run the independent-review script: its original evidence stays intact. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_ATTACHMENT_URL||'http://127.0.0.1:8067',out=path.resolve(process.env.SRA_ATTACHMENT_SHOTS||'docs/attachment-selection-race-fix');
const variants=['same-name-different','reselect','remove-reselect','refresh-recovery','same-bytes-new-selection','missing-manifest','mismatched-manifest','mismatched-name'];
const fileKey='sra.ch.file.new/draft';
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true}),results=[];
 const wait=async fn=>{let last;for(let i=0;i<100;i++){last=await fn();if(last)return last;await new Promise(r=>setTimeout(r,80));}throw new Error('Saved server state did not settle');};
 try{for(const variant of variants){
  const ctx=await browser.newContext({viewport:{width:1440,height:900}}),page=await ctx.newPage(),errors=[],outside=[];page.on('pageerror',e=>errors.push(e.message));
  await ctx.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(r.request().url()),r.abort()));
  const get=async u=>{const r=await page.request.get(base+u);assert.ok(r.ok(),await r.text());return r.json();};
  const ready=()=>page.locator('[data-ref="send"]:not(:disabled)').waitFor();
  const meta=()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),fileKey);
  const pick=async text=>{const chooser=page.waitForEvent('filechooser');await page.locator('[data-ref="import"]').click();await(await chooser).setFiles({name:'same-name.txt',mimeType:'text/plain',buffer:Buffer.from(text.repeat(30))});};
  const aText=`ORIGINAL_A_${variant}. `,bText=variant==='same-bytes-new-selection'?aText:`DIFFERENT_B_${variant}. `,bInput=`研究B ${variant}：另一份同名论文，保留这段尚未发送的文字。`;
  let release,createdA;const gate=new Promise(r=>release=r);let enter;const entered=new Promise(r=>enter=r);let first=true;
  try{
   const uploadsBefore=(await get('/__reliability__/state')).uploads;
   await page.goto(base+'/workspace');await ready();await pick(aText);const selectedA=await meta();
   await page.route('**/api/conversations/start',async r=>{if(!first)return r.continue();first=false;const response=await r.fetch();createdA=await response.json();enter();await gate;await r.fulfill({response});});
   await page.locator('[data-ref="input"]').fill(`研究A ${variant}：根据联邦学习论文明确范围。`);await page.locator('[data-ref="send"]').click();await page.getByRole('combobox',{name:'确认研究领域',exact:true}).selectOption('ai');await page.getByRole('button',{name:'确认并继续提交',exact:true}).click();await entered;
   await page.locator('#ch-new').click();await ready();await page.locator('[data-ref="input"]').fill(bInput);await pick(bText);
   const firstB=await meta();
   if(variant==='reselect')await pick(bText);
   if(variant==='remove-reselect'){await page.getByRole('button',{name:'移除待上传文件',exact:true}).click();assert.equal(await meta(),null);await pick(bText);}
   const selectedB=await meta();assert.ok(selectedB.selection_id,'Every selection needs its own identity');assert.notEqual(selectedA.selection_id,selectedB.selection_id);
   if(['reselect','remove-reselect'].includes(variant))assert.notEqual(firstB.selection_id,selectedB.selection_id);
   if(variant==='same-name-different')await page.screenshot({path:path.join(out,'before-a-response-desktop.png')});
   release();
   const aPapers=await wait(async()=>{const data=await get(`/api/projects/${createdA.project_id}/papers`);return data.papers.length===1?data.papers:null;});
   await wait(async()=>{const data=await get(`/api/projects/${createdA.project_id}/conversations/${createdA.conversation_id}/messages`);return data.messages.filter(m=>m.role==='assistant').length===1;});
   assert.equal(new URL(page.url()).searchParams.get('project'),null,'Late A cannot navigate B');
   assert.equal(await page.locator('[data-ref="input"]').inputValue(),bInput);assert.equal((await meta()).selection_id,selectedB.selection_id);
   assert.match(await page.locator('[data-ref="filePending"]').innerText(),/待提交资料：same-name.txt/);
   assert.equal(aPapers[0].abstract,aText.repeat(30));if(bText!==aText)assert.ok(!aPapers[0].abstract.includes(bText.trim()));
   if(variant==='same-name-different')await page.screenshot({path:path.join(out,'after-a-response-desktop.png')});
   let beforeSend=await meta();
   if(variant==='refresh-recovery'){
    await page.reload();await ready();assert.match(await page.locator('[data-ref="filePending"]').innerText(),/无法在刷新后恢复/);assert.equal((await meta()).selection_id,selectedB.selection_id);assert.equal(await page.locator('[data-ref="input"]').inputValue(),bInput);
    const count=(await get('/api/projects?limit=5')).total,model=(await get('/__reliability__/state')).model_calls.length;
    await page.locator('[data-ref="send"]').click();await page.locator('[data-ref="error"]').filter({hasText:/请重新选择待上传文件/}).waitFor();
    assert.equal((await get('/api/projects?limit=5')).total,count);assert.equal((await get('/__reliability__/state')).model_calls.length,model);
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'refresh-needs-file-mobile.png')});await pick(bText);beforeSend=await meta();assert.notEqual(beforeSend.selection_id,selectedB.selection_id);
   }
   if(variant==='missing-manifest')await page.evaluate(key=>localStorage.removeItem(key),fileKey);
   if(['mismatched-manifest','mismatched-name'].includes(variant)){
    await page.evaluate(({key,variant})=>{const m=JSON.parse(localStorage.getItem(key));if(variant==='mismatched-name')m.name='not-the-selected-file.txt';else m.selection_id='wrong-owner';localStorage.setItem(key,JSON.stringify(m));},{key:fileKey,variant});
    const count=(await get('/api/projects?limit=5')).total;
    await page.locator('[data-ref="send"]').click();await page.locator('[data-ref="error"]').filter({hasText:/附件选择状态不一致/}).waitFor();assert.equal((await get('/api/projects?limit=5')).total,count);assert.equal(await page.locator('[data-ref="input"]').inputValue(),bInput);
    await pick(bText);beforeSend=await meta();
   }
   await page.locator('[data-ref="send"]').click();
   await page.waitForFunction(()=>!!new URL(location.href).searchParams.get('project') || !!document.querySelector('dialog[open] select[aria-label="确认研究领域"]'));
   const confirmation=page.getByRole('combobox',{name:'确认研究领域',exact:true});if(await confirmation.isVisible()){await confirmation.selectOption('ai');await page.getByRole('button',{name:'确认并继续提交',exact:true}).click();}
   await page.locator('.ch-message-assistant').last().waitFor();await ready();const url=new URL(page.url()),pid=url.searchParams.get('project'),cid=url.searchParams.get('conversation');assert.notEqual(pid,createdA.project_id);
   const bPapers=(await get(`/api/projects/${pid}/papers`)).papers;assert.equal(bPapers.length,1,'B must actually upload its own chosen File');assert.equal(bPapers[0].abstract,bText.repeat(30));if(bText!==aText)assert.ok(!bPapers[0].abstract.includes(aText.trim()));
   assert.equal((await get(`/api/projects/${createdA.project_id}/papers`)).papers.length,1);
   assert.equal((await get('/__reliability__/state')).uploads-uploadsBefore,2,'One upload/parser call for each project');
   const msgs=(await get(`/api/projects/${pid}/conversations/${cid}/messages`)).messages;assert.equal(msgs.filter(m=>m.role==='user').length,1);assert.equal(msgs.filter(m=>m.role==='assistant').length,1);assert.equal(msgs.find(m=>m.role==='user').content,bInput);
   if(variant==='same-name-different')await page.screenshot({path:path.join(out,'b-delivered-desktop.png')});
   if(variant==='refresh-recovery'){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,'b-delivered-mobile.png')});}
   assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
   results.push({variant,passed:true,a_selection:selectedA.selection_id,b_selection:selectedB.selection_id,b_submitted_selection:beforeSend.selection_id,A:{pid:createdA.project_id,count:aPapers.length,content:aText.trim()},B:{pid,count:bPapers.length,content:bText.trim()},upload_calls:2,errors});
  }catch(e){await page.screenshot({path:path.join(out,'failure-'+variant+'.png')});throw e;}finally{release?.();await ctx.close();}
 }}finally{await browser.close();fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:results.length===variants.length,results,real_model_calls:0,real_source_calls:0},null,2));}
 console.log(`PASS ${results.length} attachment-selection variants; A/B contents and counts verified`);
})().catch(e=>{console.error(e);process.exitCode=1;});
