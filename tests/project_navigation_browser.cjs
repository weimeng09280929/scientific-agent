/* 153 TEMP projects, real FastAPI/SQLite. No research/model POST in this suite. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const base=process.env.SRA_NAV_TEST_URL || 'http://127.0.0.1:8014';
const shots=path.resolve(process.env.SRA_ACCEPTANCE_SHOTS || 'docs/conversation-home/step4a');
(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],requests=[],writes=[];
  page.setDefaultTimeout(30000);
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{requests.push(r.url());if(r.method()!=='GET')writes.push({url:r.url(),body:r.postDataJSON()});});
  const api=page.request;
  const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ref="send"]') && !document.querySelector('[data-ref="send"]').disabled);
  const route=async(pid,cid=null)=>{await page.goto(`${base}/workspace?project=${pid}&page=dashboard${cid ? '&conversation='+cid : ''}`);await ready();};
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const state=await (await api.get(base+'/__navigation__/state')).json();
  let releaseLists;const listsReady=new Promise(resolve=>releaseLists=resolve);
  try{
    assert.equal(state.total,153);
    for(const pattern of ['**/api/projects?*','**/api/conversations/recent?*'])await page.route(pattern,async r=>{const response=await r.fetch();await listsReady;try{await r.fulfill({response});}catch(e){if(!String(e).includes('Route is already handled')&&!r.request().failure())throw e;}});
    await page.goto(base+'/');await ready();
    assert.match(await page.locator('#ch-projects').innerText(),/正在读取项目/);
    assert.match(await page.locator('#ch-conversations').innerText(),/正在读取最近对话/);
    assert.doesNotMatch(await page.locator('#ch-projects').innerText(),/尚无项目/);
    releaseLists();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length===5);
    await page.waitForFunction(()=>document.querySelectorAll('#ch-projects .ch-project-link').length===5);
    await page.unroute('**/api/projects?*');await page.unroute('**/api/conversations/recent?*');
    console.log('navigation: initial five-entry lists loaded');
    assert.equal(writes.length,0);assert.equal(await page.locator('#sidebar-download').count(),0);
    assert.equal(requests.some(u=>/\/api\/projects\/[^/]+\/conversations\?/.test(u)),false);
    assert.equal(requests.filter(u=>/\/api\/projects\?/.test(u)).every(u=>new URL(u).searchParams.has('limit')),true);
    await page.locator('#ch-project-expand').click();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-projects .ch-project-link').length===10);
    await page.getByRole('button',{name:'加载更多项目',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-projects .ch-project-link').length===15);
    await page.locator('#ch-project-expand').click();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-projects .ch-project-link').length===5);
    await page.locator('.ch-history-scroll').evaluate(n=>n.scrollTop=0);await page.screenshot({path:path.join(shots,'navigation-desktop.png')});
    // Project links open a shared summary, not a silently chosen/new conversation.
    await page.locator('#ch-project-search').fill('catalog_001');
    await page.locator('#ch-projects [data-project-id="catalog_001"]').click();await ready();
    assert.equal(new URL(page.url()).searchParams.get('conversation'),null);
    await page.getByRole('heading',{name:'项目资料与对话'}).waitFor();
    await page.getByText('还没有对话。点击开始讨论，首次发送后才保存。',{exact:true}).waitFor();
    assert.equal(writes.length,0);
    await page.locator('[data-ref="downloadToggle"]').click();
    await page.getByText('当前项目还没有可下载产物。讨论、浏览或展开菜单不会生成文件。',{exact:true}).waitFor();
    await page.locator('[data-ref="downloadPanel"]').getByRole('button',{name:'收起下载',exact:true}).click();
    // Recent chats remain independent and identify same-title chats by project.
    await page.locator('#ch-conversations .ch-more-conversations').click();
    await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>8);
    const ca=state.same_titles.find(c=>c.pid===state.project_id),cb=state.same_titles.find(c=>c.pid===state.other_project);
    for(let pageIndex=0;pageIndex<4;pageIndex++){
      if(await page.locator(`#ch-conversations [data-conversation-id="${ca.cid}"]`).count() && await page.locator(`#ch-conversations [data-conversation-id="${cb.cid}"]`).count())break;
      const loaded=await page.locator('#ch-conversations .ch-conversation-row').count();
      await page.locator('#ch-conversations .ch-more-conversations').click();
      await page.waitForFunction(n=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>n,loaded);
    }
    for(const c of [ca,cb]){
      const row=page.locator(`#ch-conversations [data-project-id="${c.pid}"][data-conversation-id="${c.cid}"]`);
      assert.equal(await row.locator('.ch-project-context').count(),1);
    }
    await page.locator(`#ch-conversations [data-project-id="${cb.pid}"][data-conversation-id="${cb.cid}"] .ch-conversation-link`).click();await ready();
    await page.getByText(`仅属于项目 ${cb.pid} 的消息 0`,{exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('conversation'),cb.cid);
    await page.reload();await ready();assert.equal(new URL(page.url()).searchParams.get('project'),cb.pid);
    // Pin a non-recent current project; current project and selected chat differ.
    await page.locator('#ch-project-search').fill('');
    await page.locator(`#ch-projects [data-project-id="${cb.pid}"].selected`).waitFor();
    assert.ok(await page.locator('#ch-projects .ch-project-link').count()<=5);
    const afterReloadCount=await page.locator('#ch-conversations .ch-conversation-row').count();
    await page.locator('#ch-conversations .ch-more-conversations').click();
    await page.waitForFunction(n=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>n,afterReloadCount);
    for(let pageIndex=0;pageIndex<4 && !await page.locator(`#ch-conversations [data-conversation-id="${ca.cid}"]`).count();pageIndex++){
      const loaded=await page.locator('#ch-conversations .ch-conversation-row').count();
      await page.locator('#ch-conversations .ch-more-conversations').click();
      await page.waitForFunction(n=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>n,loaded);
    }
    await page.locator(`#ch-conversations [data-project-id="${ca.pid}"][data-conversation-id="${ca.cid}"] .ch-conversation-link`).click();await ready();
    await page.goBack();await ready();assert.equal(new URL(page.url()).searchParams.get('conversation'),cb.cid);
    await page.goForward();await ready();assert.equal(new URL(page.url()).searchParams.get('conversation'),ca.cid);
    console.log('navigation: paging, ownership and history verified');
    // Exact versions, current plan and historical outputs are discoverable.
    await page.locator('[data-ref="downloadToggle"]').click();
    await page.locator('.ch-export-row[data-export-key="artifact:method-v3"]').waitFor();
    assert.match(await page.locator('.ch-export-row[data-export-key="artifact:method-v3"]').innerText(),/v3/);
    await page.locator('.ch-export-group').filter({has:page.locator('summary',{hasText:'其他已有产物与版本'})}).locator('summary').click();
    const old=page.locator('.ch-export-row[data-export-key="artifact:method-v1"]');
    assert.match(await old.innerText(),/v1/);
    const oldHref=await old.locator('a').getAttribute('href');const exact=await api.get(base+oldHref);assert.equal(exact.status(),200);assert.match(await exact.text(),/方法正文 v1/);
    const [download]=await Promise.all([page.waitForEvent('download'),old.locator('a').click()]);
    const chunks=[];for await(const chunk of await download.createReadStream())chunks.push(chunk);const downloaded=Buffer.concat(chunks).toString('utf8');
    assert.match(download.suggestedFilename(),/v1\.md$/);assert.match(downloaded,/方法正文 v1/);assert.equal(await download.failure(),null);
    await page.locator('.ch-export-group').filter({has:page.locator('summary',{hasText:'历史产物'})}).locator('summary').click();
    await noOverflow();await page.locator('[data-ref="downloadPanel"]').evaluate(n=>n.scrollTop=0);await page.locator('#nav').evaluate(n=>n.scrollTop=0);await page.screenshot({path:path.join(shots,'downloads-desktop.png')});
    const exportCount=await page.locator('.ch-export-row').count();assert.ok(exportCount>=5);
    console.log('navigation: exact-version download verified');
    // Late A inventory can never populate B's menu, even if transport delays it.
    await page.getByRole('button',{name:'收起下载',exact:true}).click();
    let hit;const started=new Promise(resolve=>hit=resolve);
    await page.route(`**/api/projects/${state.project_id}/exports?*`,async r=>{const response=await r.fetch();hit();await page.waitForTimeout(1000);await r.fulfill({response});});
    await page.locator('[data-ref="downloadToggle"]').click();await started;
    await page.evaluate(pid=>window.__sraApp.navigate({projectId:pid,conversationId:null,page:'dashboard'}),state.empty_project);await ready();
    assert.equal(await page.locator('.ch-export-row').count(),0);
    await page.locator('[data-ref="downloadToggle"]').click();await page.getByText('当前项目还没有可下载产物。讨论、浏览或展开菜单不会生成文件。',{exact:true}).waitFor();
    await page.waitForTimeout(1200);assert.equal(await page.locator('.ch-export-row').count(),0);await page.unrouteAll({behavior:'wait'});
    // Server-paged management. Selection persists as exact IDs across filters.
    await page.locator('#ch-project-manage').click();await page.getByRole('heading',{name:'管理全部项目'}).waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('.project-library-row').length===10);
    assert.equal(await page.locator('.project-library-row').count(),10);
    await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByText('第 2 / 16 页',{exact:true}).waitFor();
    await page.getByRole('button',{name:'上一页',exact:true}).click();await page.getByText('第 1 / 16 页',{exact:true}).waitFor();
    const search=page.getByRole('searchbox',{name:'搜索已有项目',exact:true});
    await search.fill('catalog_00');await page.waitForFunction(()=>document.querySelector('.project-library [role="status"]').textContent.includes('匹配 10 个'));
    await page.getByRole('checkbox',{name:'全选当前页',exact:true}).check();
    await page.getByText(/已选 10 个（含其他页/).waitFor();
    await search.fill('catalog_010');await page.waitForFunction(()=>document.querySelectorAll('.project-library-row').length===1);
    assert.equal(await page.getByRole('checkbox',{name:'选择项目 catalog_010',exact:true}).isChecked(),false);
    await page.getByText(/已选 10 个（含其他页/).waitFor();
    await page.getByRole('button',{name:'取消全部选择',exact:true}).click();
    // Explicit search-result selection is frozen; a later broader search adds none.
    await search.fill('catalog_01');await page.getByText(/匹配 10 个/).waitFor();
    await page.getByRole('button',{name:'选择全部搜索结果',exact:true}).click();await page.getByText(/已明确选择当前搜索的 10 个/).waitFor();
    await search.fill('catalog_0');await page.getByText(/匹配 100 个/).waitFor();await page.getByText(/已选 10 个（含其他页/).waitFor();
    await page.screenshot({path:path.join(shots,'management-desktop.png')});
    page.once('dialog',d=>{assert.match(d.message(),/10 个/);assert.match(d.message(),/catalog_010/);assert.doesNotMatch(d.message(),/catalog_020/);d.dismiss();});
    await page.locator('#delete-selected-projects').click();assert.equal(writes.length,0);
    await page.getByRole('button',{name:'取消全部选择',exact:true}).click();
    await search.fill('catalog_150');await page.locator('[data-project-id="catalog_150"] input').waitFor();assert.equal(await page.locator('[data-project-id="catalog_150"] input').isDisabled(),true);
    // Archive only one disposable fixture through the actual UI; no implicit scope.
    await search.fill('catalog_020');await page.getByRole('checkbox',{name:'选择项目 catalog_020',exact:true}).check();
    page.once('dialog',d=>{assert.match(d.message(),/catalog_020/);d.accept();});await page.locator('#delete-selected-projects').click();
    await page.getByText(/已归档 1 个项目，批次/).waitFor();
    assert.equal((await api.get(base+'/api/projects/catalog_020/workspace?include_chat=false')).status(),404);
    const archived=writes.filter(r=>r.url.endsWith('/batch-delete'));assert.equal(archived.length,1);assert.deepEqual(archived[0].body.project_ids,['catalog_020']);
    await search.fill(state.empty_project);await page.getByRole('checkbox',{name:`选择项目 ${state.empty_project}`,exact:true}).check();
    page.once('dialog',d=>{assert.match(d.message(),/包含当前项目/);d.accept();});await page.locator('#delete-selected-projects').click();
    await page.waitForURL(url=>!url.searchParams.has('project') && url.searchParams.get('page')==='dashboard');await ready();
    assert.equal(await page.locator('[data-ref="project"]').innerText(),'开始新研究');
    assert.equal((await api.get(`${base}/api/projects/${state.empty_project}/workspace?include_chat=false`)).status(),404);
    // Missing DB stays missing, not a restart or replacement-project suggestion.
    const original=await (await api.get(base+'/__fixture__/state')).json();await page.goto(`${base}/workspace?project=${original.missing_project}&page=dashboard`);
    await page.getByText(/数据库缺失/).first().waitFor();assert.equal(await page.getByText(/当前后端尚未加载项目导航/).count(),0);
    // Mobile actual same app, navigation and download controls are reachable.
    await page.setViewportSize({width:390,height:844});await route(state.project_id,ca.cid);await noOverflow();
    let box=await page.locator('[data-ref="send"]').boundingBox();assert.ok(box.y+box.height<=844);
    await page.locator('[data-ref="downloadToggle"]').click();await page.locator('.ch-export-row').first().waitFor();await noOverflow();
    const panelBox=await page.locator('[data-ref="downloadPanel"]').boundingBox(),formBox=await page.locator('[data-ref="form"]').boundingBox();assert.ok(panelBox.y+panelBox.height<=formBox.y-6);
    box=await page.getByRole('button',{name:'收起下载',exact:true}).boundingBox();assert.ok(box.y>=0 && box.y+box.height<844);
    await page.screenshot({path:path.join(shots,'downloads-mobile.png')});
    await page.getByRole('button',{name:'收起下载',exact:true}).click();await page.locator('#ch-menu').click();await page.locator('#ch-project-manage').waitFor({state:'visible'});if(await page.locator('#ch-conversation-collapse').isVisible())await page.locator('#ch-conversation-collapse').click();await page.waitForFunction(()=>document.querySelectorAll('#ch-projects .ch-project-link').length===5 && document.querySelectorAll('#ch-conversations .ch-conversation-row').length===5);await noOverflow();await page.locator('.ch-history-scroll').evaluate(n=>n.scrollTop=0);
    await page.screenshot({path:path.join(shots,'navigation-mobile.png')});
    await page.locator('#ch-project-manage').click();await page.locator('.project-library-row').first().waitFor();await noOverflow();
    await page.screenshot({path:path.join(shots,'management-mobile.png')});
    assert.deepEqual(errors,[]);
    assert.equal(writes.some(r=>/\/(run|resume|messages|backfill|start|guide|regenerate-method-doc)(\?|$)/.test(r.url)),false);
    console.log(JSON.stringify({projects:153,navigation:true,recentIsolation:true,summary:true,history:true,pagination:true,selectionScope:true,archive:true,downloads:true,lateDownloadIsolation:true,desktop:true,mobile:true,errors},null,2));
  }finally{releaseLists();await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
