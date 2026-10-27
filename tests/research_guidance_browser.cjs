/* Real app/API/temp SQLite. No mocked guidance endpoints or external models. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path'),fs=require('node:fs');
const base=process.env.SRA_GUIDANCE_TEST_URL || 'http://127.0.0.1:8015';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/research-guidance');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const api=page.request;
  const state=async()=> (await (await api.get(base+'/__guidance__/state')).json());
  const fixture=await (await api.get(base+'/__fixture__/state')).json(),pid=fixture.project_id;
  const rows=async(cid)=> (await (await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages?tail=true&limit=100`)).json()).messages;
  const suggestions=async(cid)=>(await (await api.get(`${base}/api/projects/${pid}/conversations/${cid}/suggestions?recent=true`)).json()).suggestions;
  const create=async(title,entry_stage='design')=>(await (await api.post(`${base}/api/projects/${pid}/conversations`,{data:{title,entry_stage,guidance_mode:'direct'}})).json()).conversation.conversation_id;
  const ready=async()=>{await page.locator('[data-ref="send"]').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled);};
  const route=async(cid=null)=>{await page.goto(`${base}/workspace?page=dashboard&project=${pid}${cid ? '&conversation='+cid : ''}`);await ready();};
  const complete=async()=>{await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled && !document.querySelector('.ch-request-status'),{},{timeout:25000});};
  const send=async(text)=>{await page.locator('#ch-input').fill(text);await page.locator('[data-ref="send"]').click();await complete();};
  const overflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  try{
    const before=(await state()).calls.length;
    await page.goto(base+'/');await ready();
    assert.match(await page.locator('[data-ref="welcome"]').innerText(),/从你现在的研究进度开始/);
    assert.equal(await page.locator('[data-entry]').count(),4);
    for(const [i,mode,stage] of [[0,'beginner','orientation'],[1,'direct','literature'],[3,'direct','literature'],[2,'direct','question']]){
      await page.locator(`[data-entry="${i}"]`).click();
      assert.equal(await page.locator('[data-ref="mode"]').inputValue(),mode);
      assert.equal(await page.locator('[data-ref="stage"]').inputValue(),stage);
      assert.equal(await page.locator(`[data-entry="${i}"]`).getAttribute('aria-pressed'),'true');
      assert.match(await page.locator('[data-ref="entryOutcome"]').innerText(),/这次可以得到/);
      assert.equal(await page.locator('[data-ref="entryUpload"]').isVisible(),i===3);
    }
    assert.equal((await state()).calls.length,before);
    await page.screenshot({path:path.join(shots,'opening-desktop.png')});
    await route();
    assert.match(await page.locator('[data-ref="summaryText"]').innerText(),/已确认问题：|待复核问题：/);
    assert.equal((await state()).calls.length,before);
    await page.screenshot({path:path.join(shots,'project-opening-desktop.png')});
    const cid=await create('推荐与操作验收');
    await route(cid);await send('GUIDANCE_PLAN 请生成计算研究方案，核对已有方案');
    let m=(await rows(cid)).at(-1);
    const snapshot=JSON.stringify([m.suggested_replies,m.actions]);
    assert.equal(m.suggested_replies.length,2);assert.deepEqual(m.actions,[]);
    const draft=(await suggestions(cid)).find(s=>s.kind==='plan'&&!s.applied_item_id);
    assert.ok(draft);
    await page.locator(`[data-plan-preview-id="${draft.suggestion_id}"]`).waitFor();
    await page.screenshot({path:path.join(shots,'reply-actions-desktop.png')});await overflow();
    const calls=(await state()).calls.length;
    await page.reload();await ready();
    assert.equal(JSON.stringify([(await rows(cid)).at(-1).suggested_replies,(await rows(cid)).at(-1).actions]),snapshot);
    assert.equal((await state()).calls.length,calls);
    assert.equal(await page.locator('[data-ref="welcome"]').isVisible(),false);
    assert.equal(await page.locator('[data-ref="continueHint"]').isVisible(),false);
    assert.equal(await page.locator('.ch-journey-next').isVisible(),true);
    // A recommendation cannot destroy a pending input. Cancel, then explicitly
    // send the recommendation while preserving the original draft.
    await page.locator('#ch-input').fill('保留这段尚未发送的草稿');
    await page.locator('.ch-reply-option').first().click();
    await page.getByRole('button',{name:'保留草稿，暂不发送',exact:true}).click();
    assert.equal(await page.locator('#ch-input').inputValue(),'保留这段尚未发送的草稿');
    assert.equal((await rows(cid)).length,2);
    await page.locator('.ch-reply-option').first().click();
    await page.getByRole('button',{name:'保留草稿并发送推荐',exact:true}).click();await complete();
    assert.equal(await page.locator('#ch-input').inputValue(),'保留这段尚未发送的草稿');
    assert.equal((await rows(cid)).filter(r=>r.content===m.suggested_replies[0].content && r.role==='user').length,1);
    await page.locator('#ch-input').fill('');
    // Dispatch two immediate clicks on the same node; the request guard is
    // synchronous even though re-rendering removes that node.
    const selected=await page.locator('.ch-reply-option').last().getAttribute('title');
    await page.locator('.ch-reply-option').last().evaluate(b=>{b.click();b.click();});await complete();
    assert.equal((await rows(cid)).filter(r=>r.role==='user' && r.content===selected).length,1);
    // Structured plans are reviewed before explicit adoption; model replies do
    // not need an additional adopt_suggestion action card.
    const adoptedBefore=(await state()).calls.length;
    await page.locator(`[data-plan-preview-id="${draft.suggestion_id}"]`).getByRole('button',{name:'打开方案',exact:true}).click();
    await page.locator(`.ch-plan-review[data-plan-id="${draft.suggestion_id}"]`).getByRole('button',{name:'采纳方案',exact:true}).click();
    await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
    assert.equal((await state()).calls.length,adoptedBefore);
    assert.ok((await suggestions(cid)).find(s=>s.suggestion_id===draft.suggestion_id).applied_item_id);
    // A separate valid draft becomes stale through an excerpt change, without
    // touching Workspace.revision; live cards must disable on materials refresh.
    const stale=await create('片段变化验收');await route(stale);await send('GUIDANCE_PLAN 请生成计算研究方案，片段变化前');
    const staleDraft=(await suggestions(stale)).find(s=>s.kind==='plan'&&!s.applied_item_id);
    await page.locator(`[data-plan-preview-id="${staleDraft.suggestion_id}"]`).getByRole('button',{name:'打开方案',exact:true}).click();
    await api.post(base+'/__fixture__/excerpt');
    await page.getByRole('button',{name:'刷新材料与草案',exact:true}).click();
    const staleAdoption=page.locator(`.ch-plan-review[data-plan-id="${staleDraft.suggestion_id}"]`).getByRole('button',{name:'依据待复核，不能直接采纳',exact:true});
    await staleAdoption.waitFor();assert.equal(await staleAdoption.isDisabled(),true);
    await page.locator('[data-ref="panelClose"]').click();
    assert.match(await page.locator(`[data-plan-preview-id="${staleDraft.suggestion_id}"]`).innerText(),/依据待复核/);
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-ref="messages"]').evaluate(n=>{n.scrollTop=n.scrollHeight;});
    await page.screenshot({path:path.join(shots,'reply-actions-mobile.png')});await overflow();
    const staleCard=await page.locator(`[data-plan-preview-id="${staleDraft.suggestion_id}"]`).boundingBox(),composeBox=await page.locator('[data-ref="form"]').boundingBox();assert.ok(staleCard.y+staleCard.height<=composeBox.y);
    const composer=await page.locator('[data-ref="send"]').boundingBox();assert.ok(composer && composer.y+composer.height<=844);
    // Only persisted whitelisted navigation, no generation from viewing.
    const settings=await create('设置卡片验收');await route(settings);await send('GUIDANCE_SETTINGS');
    const count=(await state()).calls.length;
    await page.locator('[data-action-type="open_settings"]').click();
    await page.waitForFunction(()=>document.querySelector('.ch-settings')?.open);
    assert.equal(await page.locator('.ch-settings').getAttribute('open'),'');
    assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-ref')),'mode');
    assert.equal((await state()).calls.length,count);
    await page.locator('.ch-settings summary').click();
    await page.locator('[data-action-type="open_plan"]').click();
    await page.waitForURL(/page=experiments/);
    assert.equal((await state()).calls.length,count);
    await route(settings);await page.locator('#ch-input').fill('先暂停一下吧');await page.locator('[data-ref="send"]').click();await complete();
    const stop=(await rows(settings)).at(-1);assert.deepEqual(stop.suggested_replies,[]);assert.deepEqual(stop.actions,[]);
    // Recommendations use the same isolated in-flight machinery as typed text.
    await page.setViewportSize({width:1440,height:900});
    const a=await create('迟到推荐 A'),b=await create('迟到推荐 B');await route(a);await send('GUIDANCE_DELAY');
    await page.locator('.ch-reply-option').first().click();
    await page.getByText('处理中，可切换会话或离开页面',{exact:true}).waitFor();
    await route(b);await api.post(base+'/__fixture__/release');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('.ch-messages').innerText(),'');
    await route(a);await complete();assert.equal((await rows(a)).filter(m=>m.content==='OFFLINE_WAIT 选项消息').length,1);
    await route(fixture.long_conversation);assert.equal(await page.locator('.ch-reply-option').count(),0); // old messages remain normal
    await page.goto(base+'/');await ready();await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(shots,'opening-mobile.png')});await overflow();
    assert.equal(await page.getByRole('button',{name:/立即检索|执行实验|生成论文/}).count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,consoleErrors:errors,screenshots:shots,calls:(await state()).calls.length,pid:fixture.pid}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
