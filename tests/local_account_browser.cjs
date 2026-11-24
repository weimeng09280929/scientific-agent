const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const base = 'http://127.0.0.1:8077';
const shots = path.resolve('docs/local-account-isolation');

async function login(page, email, password) {
  await page.goto(base);
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.locator('.app').waitFor({state:'visible'});
  await page.waitForFunction(email => document.querySelector('#account-email')?.textContent?.includes(email),email);
  await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
}

async function json(page,url,body) {
  return page.evaluate(async ({url,body})=>{
    const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(r.status+': '+JSON.stringify(data));return data;
  },{url,body});
}
async function navigate(page,record) {
  await page.goto(`${base}/workspace?project=${record.project_id}&conversation=${record.conversation_id}`);
  await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
}
async function choose(page,id) {
  const button=page.locator(`[data-case-option="${id}"]`);await button.waitFor();
  const old=Number(await page.locator('.ch-teaching').getAttribute('data-state-version'));
  await button.click();await page.waitForFunction(old=>{const n=document.querySelector('.ch-teaching');return Number(n?.dataset.stateVersion)>old&&n?.dataset.busy==='false';},old);
}

(async () => {
  fs.mkdirSync(shots,{recursive:true});
  const browser = await chromium.launch({channel:'msedge',headless:true});
  const a = await browser.newContext({viewport:{width:1440,height:900}});
  const b = await browser.newContext({viewport:{width:390,height:844}});
  const pa = await a.newPage(), pb = await b.newPage();
  const errors=[];
  const external=[];
  pa.on('pageerror',e=>errors.push(e.message));pb.on('pageerror',e=>errors.push(e.message));
  for(const context of [a,b])await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(external.push(r.request().url()),r.abort()));
  await a.addInitScript(()=>{
    localStorage.setItem('sra_project_id','legacy-unowned-project');
    localStorage.setItem('sra.ch.draft.new/draft',JSON.stringify({text:'Legacy unowned draft'}));
  });
  try {
    await pa.goto(base);
    await pa.locator('#account-gate').screenshot({path:path.join(shots,'login-desktop.png')});
    assert.equal(await pa.locator('.app').isVisible(),false);
    await pb.goto(base);await pb.locator('#account-gate').screenshot({path:path.join(shots,'login-narrow.png')});
    await login(pa,'tester-a@example.com','browser acceptance A');
    assert.equal(await pa.locator('[data-ref="input"]').inputValue(),'');
    await login(pb,'tester-b@example.com','browser acceptance B');
    assert(!await pa.evaluate(()=>document.cookie.includes('sra_session')));
    await pa.locator('[data-ref="input"]').fill('A private unsent draft');
    await pa.reload();await pa.locator('[data-ref="send"]:not(:disabled)').waitFor();
    assert.equal(await pa.locator('[data-ref="input"]').inputValue(),'A private unsent draft');
    assert.equal(await pb.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const madeA=await json(pa,'/api/conversations/start',{request_id:'browser-shared-start',title:'same research name'});
    const madeB=await json(pb,'/api/conversations/start',{request_id:'browser-shared-start',title:'same research name'});
    const pidA=madeA.project_id, pidB=madeB.project_id;
    assert.notEqual(pidA,pidB);
    for(const [page,record,label] of [[pa,madeA,'A'],[pb,madeB,'B']]) {
      await json(page,`/api/projects/${record.project_id}/notes`,{text:`${label} private note`});
      const task=await json(page,`/api/projects/${record.project_id}/conversations/${record.conversation_id}/research-tasks`,{request_id:'browser-shared-task',content:'请检索离线资料并整理现状',topic:'offline account research'});
      record.task_id=task.task_id;
      for(let i=0;i<100;i++) {
        const state=await json(page,`/api/projects/${record.project_id}/conversations/${record.conversation_id}/research-tasks/${task.task_id}`);
        if(['completed','partial','failed','cancelled'].includes(state.status)){assert.equal(state.status,'completed');break;}
        await page.waitForTimeout(100);
      }
      await navigate(page,record);
      await page.locator('.ch-message-assistant').last().waitFor();
    }
    const originalMessages=(await json(pa,`/api/projects/${pidA}/conversations/${madeA.conversation_id}/messages`)).messages;
    await pa.screenshot({path:path.join(shots,'user-a-desktop.png')});
    await pb.screenshot({path:path.join(shots,'user-b-narrow.png')});
    await pb.locator('#ch-menu').click();await pb.locator('#account-logout').waitFor({state:'visible'});
    await pb.screenshot({path:path.join(shots,'user-b-account-menu-narrow.png')});
    await pa.locator('[data-ref="downloadToggle"]').click();
    const entry=pa.locator(`[data-export-key^="research-task:${madeA.task_id}"]`).first();
    await entry.waitFor();
    const saving=pa.waitForEvent('download');await entry.getByRole('link',{name:'下载',exact:true}).click();
    const download=await saving;const report=fs.readFileSync(await download.path(),'utf8');
    assert.match(report,/参考文献与阅读范围/);assert.match(report,/Fixture Author/);assert.match(report,/fixture.invalid/);
    fs.writeFileSync(path.join(shots,'downloaded-report-v1.md'),report);
    await pa.getByRole('button',{name:'收起下载',exact:true}).click();
    const readB=await pb.evaluate(async pid => (await fetch(`/api/projects/${pid}/status`)).status,pidA);
    assert.equal(readB,404);
    const projectsB=await pb.evaluate(async () => (await (await fetch('/api/projects')).json()).projects.map(p=>p.project_id));
    assert(projectsB.includes(pidB));assert(!projectsB.includes(pidA));
    // Complete the actual prebuilt teaching path through explicit adoption.
    await pa.locator('#ch-new').click();
    await pa.locator('[data-teaching="directory"]').click();
    await pa.locator('.case-card').filter({hasText:'联邦学习'}).getByRole('button',{name:'开始体验',exact:true}).click();
    await choose(pa,'begin');await choose(pa,'continue');await choose(pa,'compare');
    await choose(pa,'adopt_question');await choose(pa,'small');await choose(pa,'show_plan');
    await pa.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
    await pa.locator('.ch-material-panel .ch-plan-review[open]').waitFor();
    await choose(pa,'adopt_plan');
    await pa.waitForFunction(()=>document.querySelector('.ch-teaching strong')?.textContent.includes('体验已完成'));
    await pa.screenshot({path:path.join(shots,'teaching-completed-desktop.png')});
    await pa.locator('[data-ref="panelClose"]').click();
    await navigate(pa,madeA);
    const input=pa.locator('[data-ref="input"]');await input.fill('A upload must stay with A');
    const chooser=pa.waitForEvent('filechooser');await pa.locator('[data-ref="import"]').click();
    await pa.getByRole('button',{name:'添加待上传论文',exact:true}).click();
    await(await chooser).setFiles({name:'same-name.txt',mimeType:'text/plain',buffer:Buffer.from('A private attachment text. '.repeat(30))});
    let release,entered,committedUpload;const gate=new Promise(r=>release=r),committed=new Promise(r=>entered=r);
    await pa.route('**/upload-paper',async route=>{
      const response=await route.fetch();assert(response.ok());committedUpload=await response.json();entered();await gate;
      await route.fulfill({response}).catch(()=>{}); // navigation deliberately cancels the old page
    });
    await pa.locator('[data-ref="send"]').click();await committed;
    const secondTab=await a.newPage();await secondTab.goto(base);
    await secondTab.locator('#account-controls').waitFor({state:'visible'});
    await secondTab.locator('#account-logout').click();
    await pa.locator('#account-gate').waitFor({state:'visible'});
    await login(pa,'tester-b@example.com','browser acceptance B');
    assert.equal(await pa.locator('[data-ref="input"]').inputValue(),'');
    await pa.locator('[data-ref="input"]').fill('B private draft remains');release();
    await pa.waitForTimeout(400);
    assert.equal(await pa.locator('[data-ref="input"]').inputValue(),'B private draft remains');
    assert.doesNotMatch(await pa.locator('[data-ref="filePending"]').innerText(),/same-name/);
    assert.equal(await pa.locator('#account-email').textContent().then(x=>x.includes('tester-b@example.com')),true);
    assert.equal(await pa.evaluate(async pid => (await fetch(`/api/projects/${pid}/status`)).status,pidA),404);
    await pa.reload();await pa.locator('[data-ref="send"]:not(:disabled)').waitFor();
    assert.equal(await pa.locator('[data-ref="input"]').inputValue(),'B private draft remains');
    await pa.screenshot({path:path.join(shots,'switched-to-b-desktop.png')});
    await pa.locator('#account-logout').click();await pa.locator('#account-gate').waitFor({state:'visible'});
    await pa.goBack().catch(()=>{});await pa.locator('#account-gate').waitFor({state:'visible'});
    assert.equal(await pa.locator('.app').isVisible(),false);
    const auditContext=await browser.newContext({viewport:{width:1440,height:900}});
    const auditPage=await auditContext.newPage();await login(auditPage,'tester-a@example.com','browser acceptance A');
    const aPapers=await json(auditPage,`/api/projects/${pidA}/papers`);
    assert.equal(aPapers.papers.filter(p=>p.paper_id===committedUpload.paper_id).length,1);
    assert.match(aPapers.papers.find(p=>p.paper_id===committedUpload.paper_id).abstract,/A private attachment/);
    assert.deepEqual((await json(auditPage,`/api/projects/${pidA}/conversations/${madeA.conversation_id}/messages`)).messages,originalMessages);
    await auditContext.close();
    // Revoke B's other context without notifying the page; its next protected
    // read must stop the old application and return to login.
    const me=await(await pb.request.get(base+'/api/auth/me')).json();
    assert.equal((await pb.request.post(base+'/api/auth/logout',{headers:{'X-SRA-CSRF':me.csrf_token}})).status(),200);
    await pb.evaluate(()=>fetch('/api/projects').catch(()=>null)).catch(()=>{});
    await pb.locator('#account-gate').waitFor({state:'visible'});
    assert.equal(await pb.locator('.app').isVisible(),false);
    const state=await (await pb.request.get(base+'/__account__/state')).json();
    assert.equal(state.public_calls,0);assert.deepEqual(external,[]);
    assert.equal(errors.length,0,errors.join('\n'));
    const result={ok:true,pidA,pidB,errors:errors.length,externalRequests:external.length,teaching:true,reportDownloaded:true,lateUploadIsolated:true,multiTabLogout:true,draftsScoped:true,legacyCacheNotAdopted:true,sessionLossStopsApp:true,fixture:state};
    fs.writeFileSync(path.join(shots,'browser-results.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(e=>{console.error(String(e.stack||e).replace(/browser acceptance [AB]/g,'[redacted]'));process.exitCode=1;});
