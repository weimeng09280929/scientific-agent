/* Async draft preservation and reload while REAL generation is pending. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.SRA_HOME_TEST_URL || 'http://127.0.0.1:8013';
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ref="send"]') && !document.querySelector('[data-ref="send"]').disabled);
  const complete=()=>page.waitForFunction(()=>!document.querySelector('.ch-request-status') && !document.querySelector('[data-ref="send"]').disabled);
  try{
    await page.goto(base+'/');await ready();
    await page.locator('.ch-settings > summary').click();await page.getByRole('combobox',{name:'研究领域',exact:true}).selectOption('ai');await page.locator('.ch-settings > summary').click();
    let creationStarted;const creating=new Promise(resolve=>creationStarted=resolve);
    await page.route('**/api/conversations/start',async r=>{const response=await r.fetch();creationStarted();await page.waitForTimeout(1200);await r.fulfill({response});});
    await page.locator('#ch-input').fill('异步草稿验收的首条消息');
    await page.locator('[data-ref="send"]').click();await creating;
    await page.locator('#ch-input').fill('创建等待期间继续输入的第二条草稿');
    await complete();await page.unroute('**/api/conversations/start');
    assert.equal(await page.locator('#ch-input').inputValue(),'创建等待期间继续输入的第二条草稿');
    await page.reload();await ready();
    assert.equal(await page.locator('#ch-input').inputValue(),'创建等待期间继续输入的第二条草稿');
    const q=new URL(page.url()),pid=q.searchParams.get('project'),cid=q.searchParams.get('conversation');
    let settingsStarted;const saving=new Promise(resolve=>settingsStarted=resolve);
    await page.route('**/api/projects/*/conversations/*',async r=>{
      if(r.request().method()==='PATCH'){const response=await r.fetch();settingsStarted();await page.waitForTimeout(1200);await r.fulfill({response});}
      else await r.continue();
    });
    await page.locator('.ch-settings summary').click();await page.locator('[data-ref="stage"]').selectOption('design');
    await page.locator('[data-ref="send"]').click();await saving;
    await page.locator('#ch-input').fill('保存设置期间继续输入的第三条草稿');
    await complete();await page.unroute('**/api/projects/*/conversations/*');
    assert.equal(await page.locator('#ch-input').inputValue(),'保存设置期间继续输入的第三条草稿');
    // One server user message exists; loading pending IDs must not add another
    // optimistic duplicate while request-state read is still delayed.
    const pendingText='OFFLINE_WAIT DRAFT_REFRESH 页面刷新检查';
    await page.locator('#ch-input').fill(pendingText);await page.locator('[data-ref="send"]').click();
    await page.waitForFunction(()=>document.querySelector('.ch-request-status')?.textContent.includes('处理中'));
    await page.locator('#ch-input').fill('模型生成期间的第四条草稿');
    let statusStarted;const checking=new Promise(resolve=>statusStarted=resolve);
    await page.route('**/requests/*',async r=>{const response=await r.fetch();statusStarted();await page.waitForTimeout(1200);await r.fulfill({response});});
    await page.reload();await checking;
    assert.equal(await page.locator('.ch-message-user').filter({hasText:pendingText}).count(),1);
    assert.equal(await page.locator('#ch-input').inputValue(),'模型生成期间的第四条草稿');
    await page.request.post(base+'/__fixture__/release');
    await complete();await page.unroute('**/requests/*');
    assert.equal(await page.locator('#ch-input').inputValue(),'模型生成期间的第四条草稿');
    const rows=(await (await page.request.get(`${base}/api/projects/${pid}/conversations/${cid}/messages?tail=true&limit=100`)).json()).messages;
    assert.equal(rows.filter(m=>m.role==='user').length,3);
    assert.equal(rows.filter(m=>m.role==='user' && m.content===pendingText).length,1);
    const fixture=await (await page.request.get(base+'/__fixture__/state')).json();
    await page.goto(`${base}/workspace?project=${fixture.project_id}&conversation=${fixture.long_conversation}`);await ready();
    await page.locator('[data-ref="earlier"]').click();await page.waitForFunction(()=>document.querySelectorAll('.ch-message').length===60);
    await page.locator('[data-ref="earlier"]').click();await page.waitForFunction(()=>document.querySelectorAll('.ch-message').length===77);
    await page.locator('.ch-messages').evaluate(n=>{n.scrollTop=100;});
    await page.locator('[data-ref="latest"]').waitFor({state:'visible'});
    await page.locator('[data-ref="latest"]').click();
    await page.waitForFunction(()=>{const n=document.querySelector('.ch-messages');return document.querySelectorAll('.ch-message').length===30 && n.scrollHeight-n.scrollTop-n.clientHeight<65;});
    await page.locator('[data-ref="earlier"]').click();await page.waitForFunction(()=>document.querySelectorAll('.ch-message').length===60);
    const ids=await page.locator('.ch-message').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId));assert.equal(new Set(ids).size,60);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,creationDraftPreserved:true,settingsDraftPreserved:true,refreshDraftPreserved:true,noOptimisticDuplicate:true,noAutoRetry:true,latestPageReachable:true,paginationStillContinuous:true,errors}));
  }finally{await page.request.post(base+'/__fixture__/release').catch(()=>{});await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
