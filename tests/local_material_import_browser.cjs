/* Actual main app + empty temporary SQLite service; all external clients forbidden. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.SRA_LOCAL_TEST_URL || 'http://127.0.0.1:8047';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/local-product-acceptance/materials');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage(),errors=[],external=[];
  page.on('pageerror',e=>{errors.push(e.message);console.error(e.stack);});
  page.on('request',r=>{if(!r.url().startsWith(base) && /^https?:/.test(r.url()))external.push(r.url());});
  const api=page.request;
  const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ref="send"]') && !document.querySelector('[data-ref="send"]').disabled);
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  let releaseRead;const finishRead=new Promise(resolve=>releaseRead=resolve);
  try{
    const before=(await(await api.get(base+'/__acceptance__/state')).json()).projects;
    await page.goto(base+'/');await ready();
    assert.equal((await(await api.get(base+'/__acceptance__/state')).json()).projects,before);
    await page.locator('#ch-input').fill('我想做人工智能，但没有做过科研。');
    await page.locator('[data-ref="send"]').click();
    await page.waitForFunction(()=>new URL(location.href).searchParams.has('conversation'));
    await ready();
    const query=new URL(page.url()).searchParams, pid=query.get('project'),cid=query.get('conversation');
    // Hold only real read responses, then leave the material view before they
    // arrive. Navigation aborts browser reads, not server research generation.
    await page.route('**/workspace?include_chat=false',async route=>{const response=await route.fetch();await finishRead;await route.fulfill({response}).catch(()=>{});});
    await page.getByRole('button',{name:'导入／查看论文',exact:true}).click();
    await page.getByRole('button',{name:'文献导入与浏览',exact:true}).click();
    releaseRead();await page.unroute('**/workspace?include_chat=false');
    await page.getByRole('heading',{name:'文献库',exact:true}).waitFor();
    const upload=page.locator('.paper-import');await upload.locator(':scope > summary').click();
    assert.equal(await page.getByText('上传主题描述文件',{exact:true}).count(),0);
    await upload.locator('input[type="file"]').setInputFiles({name:'offline-material.md',mimeType:'text/markdown',buffer:Buffer.from('# 自编测试材料\n仅用于软件验收，不是发表论文或实际研究结论。')});
    await upload.locator('input[type="text"]').fill('自编材料（离线验收）');
    const saved=page.waitForResponse(r=>r.url().endsWith('/upload-paper') && r.request().method()==='POST');
    await upload.getByRole('button',{name:'上传文献',exact:true}).click();
    assert.equal((await saved).status(),200);
    const card=page.locator('.list-item[data-paper-id]').filter({hasText:'自编材料（离线验收）'});
    await card.waitFor();await card.click();
    assert.match(await card.innerText(),/阅读范围[\s\S]*用户上传文本/);
    assert.match(await card.innerText(),/正文开头节选（非论文摘要）/);
    await noOverflow();await page.screenshot({path:path.join(shots,'import-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await upload.locator(':scope > summary').click();
    await noOverflow();await page.screenshot({path:path.join(shots,'import-mobile.png')});
    await upload.locator('input[type="file"]').setInputFiles({name:'empty.md',mimeType:'text/markdown',buffer:Buffer.alloc(0)});
    const failed=page.waitForResponse(r=>r.url().endsWith('/upload-paper') && r.request().method()==='POST');
    await upload.getByRole('button',{name:'上传文献',exact:true}).click();
    assert.equal((await failed).status(),400);
    await page.getByText(/上传失败：文件为空/).waitFor();
    const material=await(await api.get(`${base}/api/projects/${pid}/workspace?include_chat=false`)).json();
    assert.equal(material.papers.length,1);
    await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`);await ready();
    await page.locator('[data-ref="panelToggle"]').click();
    const papers=page.locator('[data-material-kind="papers"]');await papers.locator(':scope > summary').click();
    const source=papers.locator('.ch-paper-card');await source.locator(':scope > summary').click();
    assert.match(await source.innerText(),/用户上传文本/);await noOverflow();
    await page.screenshot({path:path.join(shots,'source-mobile.png')});
    await page.locator('[data-ref="panelClose"]').click();
    await page.reload();await ready();assert.equal(new URL(page.url()).searchParams.get('conversation'),cid);
    assert.equal((await(await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages`)).json()).messages.filter(m=>m.role==='user').length,1);
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    console.log(JSON.stringify({ok:true,actualUpload:true,scopeVisible:true,invalidUploadPreserved:true,refresh:true,desktop:true,mobile:true,errors,external,screenshots:shots}));
  }finally{releaseRead();await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
