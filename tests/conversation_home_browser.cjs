/* Real FastAPI + temporary SQLite. Intercepts only lost/late NETWORK responses;
   conversation APIs, model failure/retry, material dependencies and history run on server. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.SRA_HOME_TEST_URL || 'http://127.0.0.1:8013';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/conversation-home');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage(),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
  const api=page.request;
  let fixture;
  try {
    for(let i=0;i<60;i++){
      try{fixture=await (await api.get(base+'/__fixture__/state',{timeout:1000})).json();break;}
      catch(e){if(i===59)throw e;await page.waitForTimeout(300);}
    }
  }catch(e){await browser.close();throw e;}
  const projects=async()=> (await (await api.get(base+'/api/projects')).json()).projects;
  const current=()=>{const q=new URL(page.url());return {pid:q.searchParams.get('project'),cid:q.searchParams.get('conversation')};};
  const messages=async(pid,cid)=> (await (await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages?tail=true&limit=100`)).json()).messages;
  const ready=async()=>{await page.locator('[data-ref="send"]').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled);};
  const route=async(pid,cid)=>{await page.goto(`${base}/workspace?page=dashboard&project=${pid}${cid ? '&conversation='+cid : ''}`);await ready();};
  const send=async(text)=>{await page.locator('#ch-input').fill(text);await page.locator('[data-ref="send"]').click();};
  const complete=async()=>{await page.waitForFunction(()=>{const b=document.querySelector('[data-ref="send"]');return b && !b.disabled && !document.querySelector('.ch-request-status');},{},{timeout:25000});};
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  try{
    const before=(await projects()).length;
    await page.goto(base+'/');await ready();
    await page.getByRole('button',{name:'第一次做研究，带我入门。',exact:false}).click();
    await page.locator('#ch-new').click();
    assert.equal((await projects()).length,before);assert.equal(writes.length,0);
    assert.equal(await page.locator('#research-guide').isVisible(),false);
    for(const [index,mode,stage] of [[0,'beginner','orientation'],[1,'direct','literature'],[2,'direct','design']]){
      await page.locator(`[data-entry="${index}"]`).click();
      assert.equal(await page.locator('[data-ref="mode"]').inputValue(),mode);
      assert.equal(await page.locator('[data-ref="stage"]').inputValue(),stage);
    }
    assert.equal(writes.length,0);
    await page.screenshot({path:path.join(shots,'empty-desktop.png')});await noOverflow();
    // Chinese IME composition must not accidentally submit Ctrl+Enter.
    await page.locator('#ch-input').fill('输入法组合期间不发送');
    await page.locator('#ch-input').dispatchEvent('compositionstart');
    await page.locator('#ch-input').press('Control+Enter');
    assert.equal(writes.length,0);await page.locator('#ch-input').dispatchEvent('compositionend');
    await page.locator('.ch-settings > summary').click();await page.getByRole('combobox',{name:'研究领域',exact:true}).selectOption('ai');await page.locator('.ch-settings > summary').click();
    // Lose the initial creation response AFTER the real server has created it.
    let lost=false;
    await page.route('**/api/conversations/start',async r=>{if(!lost){lost=true;await r.fetch();await r.abort('failed');}else await r.continue();});
    await page.locator('#ch-input').fill('首次发送离线验收：研究问题如何明确');
    await page.locator('[data-ref="form"]').evaluate(form=>{form.requestSubmit();form.requestSubmit();});
    await page.getByText('核对并继续首次发送',{exact:true}).waitFor();
    assert.equal((await projects()).length,before+1);
    await page.reload();await ready();
    await page.locator('[data-ref="send"]').click();await complete();
    const first=current();assert.ok(first.pid && first.cid);
    assert.equal((await projects()).length,before+1);
    assert.equal((await messages(first.pid,first.cid)).filter(m=>m.role==='user').length,1);
    await page.unroute('**/api/conversations/start');
    await page.screenshot({path:path.join(shots,'conversation-desktop.png')});
    // Conversation settings persist independently, without moving research stage.
    const originalStage=(await (await api.get(`${base}/api/projects/${first.pid}/workspace?include_chat=false`)).json()).product_stage;
    await page.locator('.ch-settings summary').click();
    await page.locator('[data-ref="mode"]').selectOption('beginner');
    await page.locator('[data-ref="stage"]').selectOption('design');
    await page.locator('[data-ref="goal"]').fill('澄清条件，不改变正式研究主线');
    await page.locator('[data-ref="saveSettings"]').click();
    await page.getByText('讨论设置已保存，不改变项目研究进度。',{exact:true}).waitFor();
    await page.reload();await ready();
    const settings=(await (await api.get(`${base}/api/projects/${first.pid}/conversations/${first.cid}`)).json()).conversation;
    assert.equal(settings.entry_stage,'design');assert.equal(settings.guidance_mode,'beginner');
    assert.equal((await (await api.get(`${base}/api/projects/${first.pid}/workspace?include_chat=false`)).json()).product_stage,originalStage);
    page.once('dialog',d=>d.accept('会话 A · 已重命名'));
    await page.getByLabel('项目与对话操作',{exact:true}).click();await page.locator('[data-ref="rename"]').click();
    await page.getByRole('heading',{name:'会话 A · 已重命名',exact:true}).waitFor();
    page.once('dialog',d=>d.accept('离线科研项目 · 已重命名'));
    await page.locator('[data-ref="renameProject"]').click();
    await page.locator('[data-ref="project"]').getByText('离线科研项目 · 已重命名',{exact:true}).waitFor();
    // New within the project is a draft until send; same-project history isolation.
    const count=(await (await api.get(`${base}/api/projects/${first.pid}/conversations`)).json()).conversations.length;
    await page.locator('#ch-new').click();await ready();
    assert.equal(current().pid,first.pid);assert.equal(current().cid,null);
    assert.equal((await (await api.get(`${base}/api/projects/${first.pid}/conversations`)).json()).conversations.length,count);
    await send('会话 B 不能混入会话 A 历史');await complete();const second=current();
    assert.notEqual(second.cid,first.cid);
    let calls=(await (await api.get(base+'/__fixture__/state')).json()).calls;
    assert.equal(calls.at(-1).history.length,0);
    await page.goBack();await ready();assert.equal(current().cid,null);
    await page.goBack();await ready();assert.equal(current().cid,first.cid);
    await page.goForward();await ready();assert.equal(current().cid,null);
    await route(first.pid,first.cid);await page.reload();await ready();
    assert.equal(await page.locator('.ch-message-user').count(),1);
    // Pending A response cannot render in B; no client-side cancellation claim.
    await send('OFFLINE_WAIT 会话 A 的迟到回复');
    await page.waitForFunction(()=>document.querySelector('.ch-request-status')?.textContent.includes('处理中'));
    await page.locator('#ch-conversations .ch-conversation-link').filter({hasText:'会话 B'}).click();await ready();
    assert.equal(current().cid,second.cid);
    await api.post(base+'/__fixture__/release');
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('.ch-messages').getByText(/OFFLINE_WAIT/).count(),0);
    await route(first.pid,first.cid);await complete();
    assert.ok((await messages(first.pid,first.cid)).some(m=>m.role==='assistant' && m.content.includes('OFFLINE_WAIT')));
    // Offline failure persists user message, explicit retry creates one reply.
    await send('OFFLINE_FAIL 明确重试验收');
    await page.getByRole('button',{name:'重试此消息',exact:true}).waitFor();
    const failed=(await messages(first.pid,first.cid)).filter(m=>m.role==='user' && m.content.includes('OFFLINE_FAIL'));
    assert.equal(failed.length,1);
    await page.getByRole('button',{name:'重试此消息',exact:true}).click();await complete();
    assert.equal((await messages(first.pid,first.cid)).filter(m=>m.role==='user' && m.content.includes('OFFLINE_FAIL')).length,1);
    // Lose a completed message response, then query the SAME request, no resend.
    let messageLost=false;
    await page.route('**/conversations/*/messages',async r=>{if(r.request().method()==='POST' && !messageLost){messageLost=true;await r.fetch();await r.abort('failed');}else await r.continue();});
    await send('网络响应丢失但消息已完成');await complete();await page.unroute('**/conversations/*/messages');
    assert.equal((await messages(first.pid,first.cid)).filter(m=>m.role==='user' && m.content==='网络响应丢失但消息已完成').length,1);
    // A real request times out in transport while its server thread keeps working.
    await page.route('**/conversations/*/messages',async r=>{
      if(r.request().method()==='POST' && r.request().postDataJSON().content.includes('TIMEOUT')){
        try{await r.fetch({timeout:600});}catch(_){}await r.abort('timedout');
      }else await r.continue();
    });
    await send('OFFLINE_WAIT TIMEOUT 原请求状态核对');
    await page.waitForFunction(()=>document.querySelector('.ch-request-status')?.textContent.includes('处理中'));
    await page.waitForTimeout(900);
    await api.post(base+'/__fixture__/release');await complete();
    await page.unroute('**/conversations/*/messages');
    assert.equal((await messages(first.pid,first.cid)).filter(m=>m.role==='user' && m.content.includes('TIMEOUT')).length,1);
    // Shared formal research materials + adoption and excerpt invalidation.
    await route(fixture.project_id,null);await send('材料与建议采纳的软件验收');await complete();const materialChat=current();
    calls=(await (await api.get(base+'/__fixture__/state')).json()).calls;
    assert.ok(calls.at(-1).records.some(r=>r.kind==='question'));assert.ok(calls.at(-1).records.some(r=>r.kind==='plan'));
    await page.locator('[data-ref="panelToggle"]').click();
    await page.getByRole('heading',{name:'当前研究问题',exact:true}).waitFor();
    await page.locator('.ch-proposal summary').first().click();
    await page.getByRole('button',{name:'采纳到研究记录',exact:true}).first().click();
    await page.getByText('已采纳到研究记录，可继续审阅；不表示结论得到验证。',{exact:true}).waitFor();
    let shared=await (await api.get(`${base}/api/projects/${fixture.project_id}/workspace?include_chat=false`)).json();
    assert.equal(shared.workspace.items.filter(i=>i.origin==='accepted_suggestion').length,1);
    await page.screenshot({path:path.join(shots,'materials-desktop.png')});
    await page.locator('[data-ref="panelClose"]').click();await send('正文片段变更后的旧建议');await complete();
    await api.post(base+'/__fixture__/excerpt');await page.locator('[data-ref="panelToggle"]').click();
    await page.locator('.ch-proposal summary').filter({hasText:'正文片段变更后的旧建议'}).click();
    await page.getByRole('button',{name:'依据待复核，请重新讨论',exact:true}).first().waitFor();
    assert.equal(await page.getByRole('button',{name:'采纳到研究记录',exact:true}).count(),0);
    // Missing-snapshot legacy drafts stay readable, but adoption disabled.
    const convs=(await (await api.get(`${base}/api/projects/${fixture.project_id}/conversations?limit=100`)).json()).conversations;
    const legacy=convs.find(c=>c.is_legacy);assert.ok(legacy);
    await route(fixture.project_id,legacy.conversation_id);await page.locator('[data-ref="panelToggle"]').click();
    await page.locator('.ch-proposal summary').filter({hasText:'旧草案缺少依据快照'}).click();
    assert.equal(await page.getByRole('button',{name:'依据待复核，请重新讨论',exact:true}).isDisabled(),true);
    // Long history starts at newest page; prepend maintains stable order/position.
    await route(fixture.project_id,fixture.long_conversation);
    assert.equal(await page.locator('.ch-message').count(),30);
    await page.locator('[data-ref="earlier"]').click();await page.waitForFunction(()=>document.querySelectorAll('.ch-message').length===60);
    await page.locator('[data-ref="earlier"]').click();await page.waitForFunction(()=>document.querySelectorAll('.ch-message').length===77);
    assert.deepEqual(await page.locator('.ch-message').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId)),Array.from({length:77},(_,i)=>'history-'+i));
    await page.locator('.ch-messages').evaluate(n=>{n.scrollTop=100;});
    await page.locator('[data-ref="panelToggle"]').click();
    await page.waitForTimeout(3200);assert.equal(await page.locator('.ch-messages').evaluate(n=>n.scrollTop),100);
    await page.locator('[data-ref="panelClose"]').click();await page.locator('[data-ref="latest"]').click();
    await page.waitForFunction(()=>{const n=document.querySelector('.ch-messages');return n.scrollHeight-n.scrollTop-n.clientHeight<65;});
    // Server restart orphan can be viewed repeatedly and retried only by click.
    const callCount=(await (await api.get(base+'/__fixture__/state')).json()).calls.length;
    await route(fixture.project_id,fixture.orphan_conversation);await page.getByRole('button',{name:'重试此消息',exact:true}).waitFor();
    await page.reload();await ready();assert.equal((await (await api.get(base+'/__fixture__/state')).json()).calls.length,callCount);
    await page.getByRole('button',{name:'重试此消息',exact:true}).click();await complete();
    // Missing database is not an obsolete-backend message, never creates data.
    await page.goto(`${base}/workspace?project=${fixture.missing_project}`);
    await page.getByText(/项目数据不可用：数据库缺失/).first().waitFor();
    assert.equal(await page.getByText(/后端尚未加载多会话接口/).count(),0);
    assert.equal((await api.get(`${base}/api/projects/${fixture.missing_project}/workspace?include_chat=false`)).status(),404);
    // Old detailed links, management and historical exports still work.
    await page.goto(`${base}/workspace?project=${fixture.project_id}&page=papers`);await page.getByText('Offline Federated Learning Fixture',{exact:false}).first().waitFor();
    await page.goto(`${base}/workspace?project=${fixture.project_id}&page=create`);await page.locator('.project-library').waitFor();
    assert.equal((await api.get(`${base}/api/projects/${fixture.project_id}/download/paper-draft`)).status(),200);
    assert.equal((await api.get(`${base}/api/projects/${fixture.project_id}/experiments`)).status(),200);
    // Project management's existing recoverable archive remains usable in UI.
    const scratch=await (await api.post(base+'/api/conversations/start',{data:{request_id:'archive-'+Date.now(),title:'仅供浏览器归档验收的临时项目',domain:'ai'}})).json();
    await page.reload();await page.locator('.project-library').waitFor();
    await page.getByRole('searchbox',{name:'搜索已有项目',exact:true}).fill('仅供浏览器归档验收');
    await page.getByRole('checkbox',{name:`选择项目 ${scratch.project_id}`,exact:true}).check();
    page.once('dialog',d=>d.accept());await page.locator('#delete-selected-projects').click();
    await page.waitForFunction(pid=>!document.querySelector(`.project-library-row[data-project-id="${pid}"]`),scratch.project_id);
    assert.equal((await api.get(`${base}/api/projects/${scratch.project_id}/workspace?include_chat=false`)).status(),404);
    // Sidebar conversations are paged from the real API, not flattened histories.
    for(let i=0;i<9;i++)assert.equal((await api.post(`${base}/api/projects/${fixture.project_id}/conversations`,{data:{request_id:'sidebar-'+i,title:'分页对话 '+i}})).status(),200);
    await route(materialChat.pid,materialChat.cid);
    await page.locator('#ch-conversations .ch-more-conversations').click();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>8);
    // Actual legacy FastAPI mount has no new routes: capability detection is explicit.
    const oldContext=await browser.newContext({viewport:{width:1440,height:900}});await oldContext.addInitScript(()=>{window.SRA_API_BASE='/legacy';});
    const oldPage=await oldContext.newPage();await oldPage.goto(base+'/');await oldPage.getByText(/当前后端尚未加载多会话接口/).first().waitFor();await oldContext.close();
    // Narrow screen input and drawer remain visible, with no x overflow.
    await page.setViewportSize({width:390,height:844});await route(materialChat.pid,materialChat.cid);
    await noOverflow();const sendBox=await page.locator('[data-ref="send"]').boundingBox();assert.ok(sendBox.y+sendBox.height<=844);
    await page.screenshot({path:path.join(shots,'conversation-mobile.png')});
    await page.locator('[data-ref="panelToggle"]').click();await page.getByRole('button',{name:'返回对话',exact:true}).waitFor();await noOverflow();
    await page.screenshot({path:path.join(shots,'materials-mobile.png')});
    await page.getByRole('button',{name:'返回对话',exact:true}).click();
    await page.goto(base+'/');await ready();await noOverflow();
    await page.screenshot({path:path.join(shots,'empty-mobile.png')});
    await page.locator('.ch-settings summary').click();
    const settingsSend=await page.locator('[data-ref="send"]').boundingBox();assert.ok(settingsSend.y+settingsSend.height<=844);await noOverflow();
    assert.deepEqual(errors,[]);
    assert.equal(writes.some(u=>/\/(run|resume|run-topic-discovery|regenerate-method-doc|experiments\/backfill)(\?|$)/.test(u)),false);
    console.log(JSON.stringify({passed:true,realConversationAPI:true,emptyNoWrites:true,startLostResponseIdempotent:true,isolated:true,lateResponseSafe:true,explicitRetry:true,lostMessageRecovered:true,pagination:77,adoptionProtected:true,legacyPreserved:true,missingDBProtected:true,oldServiceDetected:true,viewports:['1440x900','390x844'],screenshots:shots,errors,modelCalls:'offline only'}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
