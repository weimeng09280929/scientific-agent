/* Actual app/HTTP/SQLite; only response delivery is delayed for draft races. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8088',out=path.resolve(process.env.SRA_REVIEW_SHOTS||'docs/local-resource-protection/review-20261029');
const runId=Date.now().toString(36);
const allVariants=['send','resend','reload-resend','edit-text','attachment','target','accepted','unknown','retype','original-context'];
const variants=process.env.SRA_REVIEW_VARIANT?[process.env.SRA_REVIEW_VARIANT]:allVariants;
assert(variants.every(v=>allVariants.includes(v)));
async function call(p,url,method='GET',body){return p.evaluate(async({url,method,body})=>{
 const r=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 return {status:r.status,data:await r.json()};
},{url,method,body});}
const msg=p=>`/api/projects/${p.project_id}/conversations/${p.conversation_id}/messages`;
const route=p=>`${base}/?page=dashboard&project=${p.project_id}&conversation=${p.conversation_id}`;
const ready=p=>p.locator('[data-ref="send"]:not(:disabled)').waitFor();
async function login(p){await p.goto(base);await p.locator('[name=email]').fill('tester-a@example.com');await p.locator('[name=password]').fill('browser acceptance A');await p.getByRole('button',{name:'登录',exact:true}).click();await ready(p);}
async function create(p,id){const r=await call(p,'/api/conversations/start','POST',{request_id:id,title:'版本冲突补修 '+id,domain:'ai'});assert.equal(r.status,200);return r.data;}
async function pending(p,record){return p.evaluate(key=>JSON.parse(localStorage.getItem(key)),`sra.ch.pending.${record.project_id}/${record.conversation_id}`);}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});const results=[];
 try{for(const variant of variants){
  const context=await browser.newContext({viewport:{width:variant==='reload-resend'?390:1440,height:900}});
  await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
  const page=await context.newPage(),errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
  let other,releaseResponse;
  try{
   await login(page);const blocking=await create(page,runId+'-block-'+variant),record=await create(page,runId+'-review-'+variant);
   let originalPaper,originalTarget;
   if(variant==='original-context'){
    const upload=await page.evaluate(async pid=>{
     const form=new FormData();form.append('file',new Blob(['Offline source text for receipt and citation checks.']),'original.txt');form.append('request_id','original-upload');
     const r=await fetch(`/api/projects/${pid}/upload-paper`,{method:'POST',body:form});return {status:r.status,data:await r.json()};
    },record.project_id);
    assert.equal(upload.status,200);originalPaper=upload.data.paper_id;
    const w=`/api/projects/${record.project_id}/workspace`,current=(await call(page,w)).data.workspace;
    const note=await call(page,w+'/items','POST',{expected_revision:current.revision,item:{kind:'note',title:'原讨论目标',content:'只讨论这条笔记'}});
    assert.equal(note.status,200);originalTarget=note.data.workspace.items.at(-1).item_id;
   }
   const before=await(await page.request.get(base+'/__resource__/state')).json();
   const blocker=await context.newPage();await blocker.goto(route(blocking));await ready(blocker);
   await page.request.post(base+'/__resource__/hold');
   await blocker.evaluate(url=>{window.reviewBlocked=fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:'blocking',content:'解释范围'})}).then(r=>r.status);},msg(blocking));
   assert((await(await page.request.get(base+'/__resource__/entered')).json()).entered);
   other=await context.newPage();await other.goto(route(record));await ready(other);
   page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname===msg(record))posts.push(JSON.parse(r.postData()));});
   // Switch page's selected project without affecting the already accepted work.
   await page.goto(route(record));await ready(page);const text='原请求 '+variant+' 的文字必须保留';
   if(originalPaper){
    await page.evaluate(({pid,target})=>window.dispatchEvent(new CustomEvent('sra:discuss',{detail:{projectId:pid,itemId:target}})),{pid:record.project_id,target:originalTarget});
    await page.reload();await ready(page);
    await page.locator('[data-ref="panelToggle"]').click();await page.locator('[data-tab="papers"]').click();
    await page.locator(`[data-paper-id="${originalPaper}"] summary`).click();
    await page.locator(`[data-paper-id="${originalPaper}"] input[type=checkbox]`).check();
    await page.locator('[data-ref="panelClose"]').click();
   }
   await page.locator('#ch-input').fill(text);
   const refused=page.waitForResponse(r=>new URL(r.url()).pathname===msg(record)&&r.status()===429);
   await page.locator('[data-ref="send"]').click();await refused;await ready(page);
   assert.equal(await page.locator('#ch-input').inputValue(),text);
   const original=await pending(page,record);assert(original?.capacity_rejected);
   if(originalPaper){assert.deepEqual(original.body.selected_paper_ids,[originalPaper]);assert.equal(original.body.selected_item_id,originalTarget);}
   const workspace=`/api/projects/${record.project_id}/workspace`;
   const old=(await call(other,workspace)).data.workspace;
   const update=await call(other,workspace+'/brief','PUT',{expected_revision:old.revision,brief:{resources:'只有 CPU，没有 GPU',time_budget:'四周'}});
   assert.equal(update.status,200);const revision=update.data.workspace.revision;
   await page.request.post(base+'/__resource__/release');assert.equal(await blocker.evaluate(()=>window.reviewBlocked),200);
   const conflict=page.waitForResponse(r=>new URL(r.url()).pathname===msg(record)&&r.status()===409);
   let changed=text,selection=null,target=null;
   if(variant==='edit-text'){
    let enter;const entered=new Promise(r=>enter=r),gate=new Promise(r=>releaseResponse=r);
    await page.route('**/api/projects/*/conversations/*/messages',async r=>{const response=await r.fetch();enter();await gate;await r.fulfill({response});});
    const click=page.locator('[data-ref="send"]').click();await entered;
    changed='409 等待期间的新文字，不能被覆盖';await page.locator('#ch-input').fill(changed);releaseResponse();await click;
   }else if(variant==='resend')await page.getByRole('button',{name:'用原请求重新提交',exact:true}).click();
   else await page.locator('[data-ref="send"]').click();
   await conflict;await page.getByText('研究材料版本已变化',{exact:false}).first().waitFor();await ready(page);
   if(variant==='edit-text')await page.unroute('**/api/projects/*/conversations/*/messages');
   assert.equal(await page.locator('#ch-input').inputValue(),changed);
   const stale=await pending(page,record);assert.equal(stale.body.expected_revision,original.body.expected_revision);assert(stale.needs_review);
   assert.equal(stale.body.request_id,original.body.request_id);assert.equal(posts.length,2);
   if(variant==='send')await page.screenshot({path:path.join(out,'409-input-retained-desktop.png')});
   if(['reload-resend','original-context'].includes(variant)){
    await page.reload();await ready(page);assert.equal(await page.locator('#ch-input').inputValue(),text);
    assert((await pending(page,record)).needs_review);
   }
   if(variant==='attachment'){
    await page.locator('[data-ref="import"]').click();const chooser=page.waitForEvent('filechooser');
    await page.getByRole('button',{name:'添加待上传论文',exact:true}).click();
    await(await chooser).setFiles({name:'new-selection.txt',mimeType:'text/plain',buffer:Buffer.from('new attachment')});
    selection=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),`sra.ch.file.${record.project_id}/${record.conversation_id}`);
    await page.getByText('待提交资料：new-selection.txt',{exact:false}).waitFor();
   }
   if(variant==='target'){
    const current=(await call(other,workspace)).data.workspace;
    const saved=await call(other,workspace+'/items','POST',{expected_revision:current.revision,item:{kind:'note',title:'用户新选择的目标',content:'保留新目标'}});
    assert.equal(saved.status,200);target=saved.data.workspace.items.at(-1).item_id;
    await page.evaluate(({pid,target})=>window.dispatchEvent(new CustomEvent('sra:discuss',{detail:{projectId:pid,itemId:target}})),{pid:record.project_id,target});
    await page.reload();await ready(page);
   }
   if(['edit-text','attachment','target'].includes(variant)){
    await page.locator('[data-ref="send"]').click();await page.getByText('当前文字、附件或采纳目标与原请求不同',{exact:false}).waitFor();
    assert.equal(posts.length,2);assert.equal((await pending(page,record)).body.request_id,original.body.request_id);
   }
   if(variant==='accepted'){
    const accepted=await call(other,msg(record),'POST',{...original.body,expected_revision:revision});assert.equal(accepted.status,200);
    changed='另一个标签页已完成原请求，这段新编辑仍保留';await page.locator('#ch-input').fill(changed);
    await page.getByRole('button',{name:'用原请求重新提交',exact:true}).click();await ready(page);
    assert.equal(posts.length,2);assert.equal(await page.locator('[role=dialog]').count(),0);
   }else{
    if(variant==='unknown'){
     await page.route('**/requests/'+original.body.request_id,r=>r.abort('failed'));
     await page.getByRole('button',{name:'用原请求重新提交',exact:true}).click();
     await page.getByText('原请求状态尚未确认',{exact:false}).waitFor();assert.equal(posts.length,2);
     assert.deepEqual((await pending(page,record)).body,original.body);
     await page.unroute('**/requests/'+original.body.request_id);
    }
    if(['send','original-context'].includes(variant))await page.locator('[data-ref="send"]').click();
    else await page.getByRole('button',{name:'用原请求重新提交',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'核对更新后的研究依据'});await dialog.waitFor();
    assert((await dialog.innerText()).includes('只有 CPU，没有 GPU'));
    assert.equal((await pending(page,record)).body.expected_revision,original.body.expected_revision);
    if(variant==='reload-resend')await page.screenshot({path:path.join(out,'explicit-review-narrow.png')});
    if(variant==='retype'){
     let enter;const entered=new Promise(r=>enter=r),gate=new Promise(r=>releaseResponse=r);
     await page.route('**/api/projects/*/conversations/*/messages',async r=>{const response=await r.fetch();enter();await gate;await r.fulfill({response});});
     const clicking=dialog.getByRole('button',{name:'确认已核对，继续原请求'}).click();await entered;
     await page.locator('#ch-input').fill('');await page.locator('#ch-input').fill(text);
     releaseResponse();await clicking;
    }else await dialog.getByRole('button',{name:'确认已核对，继续原请求'}).click();
    await page.getByText('离线资源验收回复',{exact:false}).first().waitFor();await ready(page);
    assert.equal(posts.length,3);assert.equal(posts[2].request_id,original.body.request_id);
    assert.deepEqual({...posts[2],expected_revision:original.body.expected_revision},{...posts[0],expected_revision:original.body.expected_revision});
   }
   const saved=(await call(page,msg(record)+'?tail=true&limit=100')).data.messages;
   assert.deepEqual(saved.map(m=>m.role),['user','assistant']);assert.equal(saved[0].content,text);
   assert.equal(await page.locator('#ch-input').inputValue(),['edit-text','attachment','target','accepted','retype'].includes(variant)?changed:'');
   if(selection){const now=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),`sra.ch.file.${record.project_id}/${record.conversation_id}`);assert.equal(now.selection_id,selection.selection_id);}
   if(target){const draft=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),`sra.ch.draft.${record.project_id}/${record.conversation_id}`);assert.equal(draft.selectedItemId,target);}
   const after=await(await page.request.get(base+'/__resource__/state')).json();
   assert.equal(after.guidance_calls-before.guidance_calls,2);assert.equal(after.heavy,0);assert.equal(after.public_calls,0);
   assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   results.push({variant,passed:true,requestId:original.body.request_id,messageCount:saved.length,targetModelExecutions:1});
  }catch(e){await page.screenshot({path:path.join(out,'failure-'+variant+'.png')});throw e;}
  finally{releaseResponse?.();await page.request.post(base+'/__resource__/release').catch(()=>{});await context.close();}
 }}finally{await browser.close();fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:results.length===variants.length,results,realModelCalls:0,publicCalls:0},null,2));}
 console.log('PASS '+results.length+' revision recovery browser variants');
})().catch(e=>{console.error(String(e.stack||e).replace(/browser acceptance A/g,'[redacted]'));process.exitCode=1;});
