/* Step 5 browser acceptance: real app/API/temp SQLite, offline retrieval fixtures.
   Desktop + narrow viewport task cards; no fabricated progress; pageerror must stay empty. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path'),fs=require('node:fs');
const base=process.env.SRA_RESEARCH_TEST_URL || 'http://127.0.0.1:8017';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/conversation-research-tasks');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  if(process.env.SRA_RT_NETLOG) page.on('response',async r=>{const u=r.url();if(u.includes('/research-tasks/task-')){let s='';try{s=(await r.json()).status;}catch(_){s='?';}console.log('NET',r.request().method(),u.slice(-16),s);}});
  const api=page.request;
  const state=async()=> (await (await api.get(base+'/__research__/state')).json());
  const setMode=async(mode)=> (await (await api.post(base+'/__research__/mode',{data:{mode}})).json());
  const fixture=await (await api.get(base+'/__fixture__/state')).json(),pid=fixture.project_id;
  const create=async(title)=>(await (await api.post(`${base}/api/projects/${pid}/conversations`,{data:{title,guidance_mode:'direct',entry_stage:'literature'}})).json()).conversation.conversation_id;
  const ready=async()=>{await page.locator('[data-ref="send"]').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled);};
  const route=async(cid)=>{await page.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`);await ready();};
  const send=async(text)=>{await page.locator('#ch-input').fill(text);await page.locator('[data-ref="send"]').click();};
  const card=()=>page.locator('.ch-task-card').last();
  const waitStatus=async(labelRegex,timeout=40000)=>{try{await card().locator('summary').filter({hasText:labelRegex}).waitFor({timeout});}catch(e){const texts=await page.locator('.ch-task-card').allInnerTexts().catch(()=>['<none>']);console.error('WAIT_TIMEOUT',String(labelRegex),'cards:',JSON.stringify(texts).slice(0,500));throw e;}};
  const overflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  try{
    // ---- 场景 1：明确检索请求 → 任务卡 → 完成 → 交付消息 + 导出 ----
    const cid1=await create('检索任务验收');
    await route(cid1);
    await send('帮我查一下联邦学习隐私保护方向的代表性论文');
    await card().waitFor({timeout:15000});
    await card().locator('summary').filter({hasText:/排队中|运行中|已完成|部分完成/}).waitFor({timeout:15000});
    await waitStatus(/已完成|部分完成/);
    await page.screenshot({path:path.join(shots,'task-completed-desktop.png')});
    // 交付消息进入原会话
    await page.locator('.ch-message').filter({hasText:'检索与整理'}).first().waitFor({timeout:20000});
    // 任务卡：真实步骤、候选数、来源明细可展开；无编造百分比
    const summary=await card().locator('> summary').innerText();
    assert.match(summary,/文献检索：/);
    assert.doesNotMatch(await card().innerText(),/%\d+|完成度 \d/);
    await card().locator('> summary').click(); // 展开
    await card().locator('.ch-task-detail summary').click();
    assert.match(await card().innerText(),/openalex|arxiv/);
    const calls=(await state()).calls.length;
    assert.ok(calls>0,'来源调用应已发生');
    await overflow();
    // 下载目录列出实际保存的报告
    await page.locator('[data-ref="downloadToggle"]').click();
    await page.locator('.ch-download-panel').waitFor();
    await page.locator('.ch-download-panel').getByText('研究现状整理').first().waitFor({timeout:15000});
    assert.match(await page.locator('.ch-download-panel').innerText(),/研究现状整理/);
    await page.locator('[data-ref="downloadToggle"]').click();

    // ---- 场景 2：部分来源失败 → 部分完成 + 失败来源标注 ----
    await setMode('partial');
    const cid2=await create('部分完成验收');
    await route(cid2);
    await send('帮我查一下差分隐私的代表性论文');
    await card().waitFor({timeout:15000});
    await waitStatus(/部分完成/);
    assert.match(await card().locator('> summary').innerText(),/失败来源/);
    await page.screenshot({path:path.join(shots,'task-partial-desktop.png')});
    await overflow();

    // ---- 场景 2b：partial 补跑 → 仅重试失败来源 → 报告版本 v2 + 补跑交付消息 ----
    // 补跑期间旧报告下载入口始终可见；成功后来源完整性更新。
    await card().locator('> summary').click(); // 展开 partial 卡片
    await card().locator('button', {hasText:'下载整理报告'}).waitFor({timeout:5000}); // 旧报告可下载
    await setMode('ok'); // 夹具：失败来源（arxiv）恢复
    await card().locator('button', {hasText:'补跑失败来源'}).click();
    // "部分完成"含"完成"但不含"已完成"：/已完成/ 只匹配补跑修复后的终态。
    await waitStatus(/已完成/,40000);
    // 状态刷新会重建任务卡（默认折叠）：重新展开后再校验版本列表。
    await card().locator('> summary').click();
    await page.locator('.ch-task-card').last().getByText(/报告版本/).waitFor({timeout:20000});
    const partialCardText=await card().innerText();
    assert.match(partialCardText,/v1/); // 旧版本仍在列表中
    assert.match(partialCardText,/v2/); // 补跑生成新版本
    await page.screenshot({path:path.join(shots,'task-resume-completed.png')});
    await overflow();

    // ---- 场景 3：全部来源失败 → 失败 + 重试入口，不生成伪综述 ----
    await setMode('fail');
    const cid3=await create('全部失败验收');
    await route(cid3);
    await send('帮我查一下成员推断攻击的论文');
    await card().waitFor({timeout:15000});
    // A running card can already list failed sources; that is not terminal failure.
    await waitStatus(/ · 失败(?: ·|$)/);
    await card().locator('> summary').click(); // 展开失败卡片，重试入口可见
    assert.match(await card().innerText(),/重试/);
    await page.screenshot({path:path.join(shots,'task-failed-desktop.png')});
    await setMode('ok');

    // ---- 场景 4：模糊请求 → 澄清卡片 → 编辑范围后启动 ----
    const cid4=await create('澄清卡片验收');
    await route(cid4);
    await send('帮我查一下相关论文');
    await page.locator('.ch-action-card[data-action-type="start_search"]').waitFor({timeout:15000});
    await page.screenshot({path:path.join(shots,'clarify-card-desktop.png')});
    await page.locator('.ch-action-card[data-action-type="start_search"]').click();
    await page.locator('.ch-search-scope').waitFor();
    await page.locator('.ch-search-scope input').first().fill('联邦学习隐私保护');
    await page.locator('.ch-search-scope button', {hasText:'开始检索'}).click();
    await card().waitFor({timeout:15000});
    await waitStatus(/已完成|部分完成/);

    // ---- 场景 5：运行中取消 → 已取消，无交付消息 ----
    const cid5=await create('取消验收');
    await setMode('blocked');
    await route(cid5);
    await send('帮我查一下梯度反演的论文');
    await card().waitFor({timeout:15000});
    await card().locator('button', {hasText:'取消检索'}).waitFor({timeout:15000});
    await card().locator('button', {hasText:'取消检索'}).click();
    // 外部调用无法即时中断：先确认服务端已登记取消，再释放夹具阻塞。
    let registered=false;
    for(let i=0;i<20 && !registered;i++){
      await page.waitForTimeout(300);
      const tasksNow=await (await api.get(`${base}/api/projects/${pid}/conversations/${cid5}/research-tasks`)).json();
      registered=tasksNow.tasks[0].cancel_requested===true;
    }
    assert.equal(registered,true,'取消请求应已登记');
    await (await api.post(base+'/__research__/release')).json();
    await waitStatus(/已取消/,30000);
    await page.waitForTimeout(800);
    const rows5=await (await api.get(`${base}/api/projects/${pid}/conversations/${cid5}/messages?tail=true&limit=100`)).json();
    assert.ok(!rows5.messages.some(m=>m.role==='assistant' && m.content.includes('检索与整理已完成')),'取消后不得发布最终结果');
    await page.screenshot({path:path.join(shots,'task-cancelled-desktop.png')});
    await setMode('ok');

    // ---- 场景 6：窄屏 390×844 任务卡可用 ----
    const mobile=await browser.newContext({viewport:{width:390,height:844}});
    const mpage=await mobile.newPage();
    mpage.on('pageerror',e=>errors.push('mobile:'+e.message));
    const mapi=mpage.request;
    await mpage.goto(`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid1}`);
    await mpage.locator('[data-ref="send"]').waitFor();
    await mpage.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled);
    await mpage.locator('.ch-task-card').first().waitFor({timeout:15000});
    assert.equal(await mpage.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await mpage.screenshot({path:path.join(shots,'task-mobile.png'),fullPage:false});
    await mobile.close();

    assert.deepEqual(errors,[],'页面不得出现未处理异常：'+errors.join('; '));
    console.log('STEP5_BROWSER_OK');
  }finally{
    await browser.close();
  }
})().catch(e=>{console.error('STEP5_BROWSER_FAIL',e);process.exit(1);});
