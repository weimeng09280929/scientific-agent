/* Actual index.html + real APIs/temp SQLite. Only model output is an offline fixture. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_PLAN_TEST_URL || 'http://127.0.0.1:8018',shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/research-plan-templates');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());const api=page.request;
  const fixture=await (await api.get(base+'/__fixture__/state')).json(),pid=fixture.project_id;
  const state=async()=>(await(await api.get(base+'/__plans__/state')).json());
  const material=async()=>(await(await api.get(`${base}/api/projects/${pid}/workspace?include_chat=false`)).json());
  const ready=async()=>{await page.locator('[data-ref="send"]').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled);};
  const complete=async()=>{await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled && !document.querySelector('.ch-request-status'),{},{timeout:30000});};
  const route=async(cid)=>{await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`);await ready();};
  const create=async(title)=>(await(await api.post(`${base}/api/projects/${pid}/conversations`,{data:{title,entry_stage:'design',guidance_mode:'direct'}})).json()).conversation.conversation_id;
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  let cids={};
  try{
    const original=await material(),originalItems=original.workspace.items.length,oldPlan=original.workflow.flow.plan;
    for(const method of ['computational','theoretical','experimental']){
      const cid=cids[method]=await create('方案验收 · '+method);await route(cid);
      const before=(await state()).calls.length;
      await page.locator('.ch-plan-tools summary').click();
      await page.getByRole('combobox',{name:'方案研究方法',exact:true}).selectOption(method);
      assert.equal((await state()).calls.length,before);
      await page.getByRole('button',{name:'准备生成方案请求',exact:true}).click();
      assert.equal((await state()).calls.length,before);
      await page.locator('[data-ref="send"]').click();await complete();
      assert.equal((await material()).workspace.items.length,originalItems);
      await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
      const review=page.locator('.ch-material-panel .ch-plan-review[open]').last();
      assert.equal(await review.locator('.ch-plan-metadata').evaluate(n=>n.open),false);
      assert.ok(!(await review.innerText()).includes('stem-plan-v1'),'Template code stays inside collapsed technical details');
      const text=await review.innerText();assert.match(text,/未采纳讨论稿/);assert.match(text,/结构与依据检查/);
      if(method==='theoretical'){assert.match(text,/证明义务/);assert.ok(!text.includes('训练集'));}
      if(method==='experimental'){assert.match(text,/测量方法/);assert.match(text,/专业审核/);}
      await review.scrollIntoViewIfNeeded();await noOverflow();
      await page.screenshot({path:path.join(shots,method+'-desktop.png')});
      await review.locator('.ch-rich h2,.ch-rich h3').filter({hasText:'方法专属'}).first().scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(shots,method+'-method-desktop.png')});
      // Actual download stream and body, no synthetic download menu.
      const downloadPromise=page.waitForEvent('download');await review.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();
      const download=await downloadPromise,body=fs.readFileSync(await download.path(),'utf8');assert.match(body,/未采纳讨论稿/);assert.match(body,/已有研究问题/);
    }
    // Revise the old selected record, not a newly invented target. This is a
    // distinct request, generated draft first; final adoption is a separate POST.
    await route(cids.theoretical);await page.locator('[data-ref="panelToggle"]').click();
    const saved=page.locator('[data-ref="materialBody"] .ch-plan-review').first();await saved.locator(':scope > summary').click();
    await saved.getByRole('button',{name:'请求修订方案',exact:true}).click();
    await page.locator('[data-ref="panelClose"]').click();
    await page.locator('.ch-plan-tools summary').click();await page.getByRole('combobox',{name:'方案研究方法',exact:true}).selectOption('theoretical');
    await page.locator('.ch-plan-tools summary').click();await page.locator('[data-ref="send"]').click();await complete();
    const rows=(await(await api.get(`${base}/api/projects/${pid}/conversations/${cids.theoretical}/suggestions?recent=true`)).json()).suggestions;
    assert.equal(rows[0].supersedes_id,oldPlan.item_id);
    assert.equal((await material()).workflow.flow.plan.item_id,oldPlan.item_id);
    assert.equal(await page.locator('.ch-messages .ch-proposal-preview[data-plan-preview-id]').last().getAttribute('data-plan-preview-id'),rows[0].suggestion_id,'Latest displayed plan must identify the latest returned draft');
    await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
    const newest=page.locator(`.ch-material-panel .ch-plan-review[data-plan-id="${rows[0].suggestion_id}"]`);await newest.waitFor({state:'visible'});
    assert.match(await newest.innerText(),/本次修订变化/);
    await newest.getByRole('button',{name:'采纳方案',exact:true}).click();
    await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
    const updated=await material(),current=updated.workflow.flow.plan;
    assert.equal(current.plan_version,2);assert.equal(current.supersedes_id,oldPlan.item_id);assert.equal(current.plan.method,'theoretical');
    assert.equal(updated.workspace.items.find(i=>i.item_id===oldPlan.item_id).status,'superseded');
    assert.ok(updated.plan_reviews[current.item_id]);
    const count=(await state()).calls.length;await page.reload();await ready();assert.equal((await state()).calls.length,count);
    await page.getByRole('button',{name:'下载当前产物',exact:true}).click();
    await page.locator(`[data-export-key="plan:${current.item_id}"]`).waitFor();
    const formalRow=page.locator(`[data-export-key="plan:${current.item_id}"]`);assert.match(await formalRow.innerText(),/v2/);
    const downloadPromise=page.waitForEvent('download');await formalRow.getByRole('link',{name:'下载',exact:true}).click();const dl=await downloadPromise;
    const official=fs.readFileSync(await dl.path(),'utf8');assert.match(official,/版本：2/);assert.match(official,/理论研究/);assert.match(official,/当前正式方案/);
    await page.screenshot({path:path.join(shots,'versions-download-desktop.png')});
    await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'收起下载',exact:true}).click();
    await page.locator('[data-ref="panelToggle"]').click();const mobile=page.locator('[data-ref="materialBody"] .ch-plan-review').first();await mobile.locator(':scope > summary').click();
    await page.screenshot({path:path.join(shots,'review-mobile.png')});await noOverflow();await page.locator('[data-ref="panelClose"]').click();
    await page.getByRole('button',{name:'下载当前产物',exact:true}).click();await page.locator(`[data-export-key="plan:${current.item_id}"]`).waitFor();
    await page.screenshot({path:path.join(shots,'download-mobile.png')});await noOverflow();
    const mobileDownload=page.waitForEvent('download');await page.locator(`[data-export-key="plan:${current.item_id}"]`).getByRole('link',{name:'下载',exact:true}).click();
    assert.match(fs.readFileSync(await(await mobileDownload).path(),'utf8'),/版本：2/);
    await page.getByRole('button',{name:'收起下载',exact:true}).click();await page.locator('[data-ref="panelToggle"]').click();
    const reviseMobile=page.locator('[data-ref="materialBody"] .ch-plan-review').first();if(!await reviseMobile.evaluate(n=>n.open))await reviseMobile.locator(':scope > summary').click();
    await reviseMobile.getByRole('button',{name:'请求修订方案',exact:true}).click();await page.locator('[data-ref="panelClose"]').click();
    await page.locator('[data-ref="send"]').click();await complete();
    assert.equal((await material()).workflow.flow.plan.item_id,current.item_id);
    await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();
    const mobileDraft=page.locator('.ch-material-panel .ch-plan-review[open]').last();
    assert.match(await mobileDraft.innerText(),/未采纳讨论稿/);
    const mobileDraftDownload=page.waitForEvent('download');await mobileDraft.getByRole('link',{name:'下载此版本（MD）',exact:true}).click();
    assert.match(fs.readFileSync(await(await mobileDraftDownload).path(),'utf8'),/未采纳讨论稿/);
    await page.screenshot({path:path.join(shots,'revision-mobile.png')});await noOverflow();
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,methods:3,draftNotAdopted:true,revisionVersion:2,realDownloads:true,errors,screenshots:shots,pid:fixture.pid}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
