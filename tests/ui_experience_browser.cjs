/* Step 8B real main app / temporary FastAPI, no external calls. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.SRA_UI_URL||'http://127.0.0.1:8055',before=process.argv.includes('--before');
const shots=path.resolve('docs/ui-experience-unification/'+(before?'before':'after'));
(async()=>{
 fs.mkdirSync(shots,{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));const api=page.request;
 const ready=()=>page.waitForFunction(()=>document.querySelector('[data-ref="send"]')&&!document.querySelector('[data-ref="send"]').disabled);
 const screenshot=async name=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(shots,name+'.png')});};
 try{
  await page.goto(base+'/');await ready();await page.waitForFunction(()=>!document.querySelector('#ch-conversations')?.textContent.includes('正在读取'));await screenshot('home-desktop');
  await page.setViewportSize({width:390,height:844});await screenshot('home-mobile');await page.setViewportSize({width:1440,height:900});
  if(!before){
   const count=(await(await api.get(base+'/api/projects')).json()).projects.length;
   const callCount=(await(await api.get(base+'/__reliability__/state')).json()).model_calls.length;
   assert.equal(await page.locator('[data-ref="domain"]').inputValue(),'');
   await page.locator('[data-entry="0"]').click();
   assert.equal((await(await api.get(base+'/api/projects')).json()).projects.length,count);
   const raw='我想研究联邦学习中客户端数据分布不同造成的比较困难，希望先明确一个可回答的问题，预算和设备尚未确定。';
   await page.locator('#ch-input').fill(raw);await page.locator('[data-ref="send"]').click();
   assert.equal(new URL(page.url()).searchParams.has('project'),false);
   assert.equal((await(await api.get(base+'/api/projects')).json()).projects.length,count);
   assert.equal((await(await api.get(base+'/__reliability__/state')).json()).model_calls.length,callCount);
   assert.equal(await page.locator('#ch-input').inputValue(),raw);
   await page.getByRole('combobox',{name:'研究领域',exact:true}).selectOption('ai');await page.locator('.ch-settings > summary').click();
   await page.locator('[data-ref="send"]').dblclick();
   await page.waitForFunction(()=>new URL(location.href).searchParams.has('conversation'));
   await page.waitForFunction(()=>!document.querySelector('[data-ref="send"]').disabled&&!document.querySelector('.ch-request-status'));
   const p=new URL(page.url()).searchParams,pid=p.get('project'),cid=p.get('conversation');
   const messages=(await(await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages`)).json()).messages;
   assert.equal(messages.filter(m=>m.role==='user').length,1);assert.equal(messages.find(m=>m.role==='user').content,raw);
   const meta=(await(await api.get(`${base}/api/projects/${pid}/conversations/${cid}`)).json()).conversation;
   assert([...meta.title].length<=30);assert.notEqual(meta.title,raw);
   assert.equal((await(await api.get(base+'/__reliability__/state')).json()).model_calls.length,callCount+1);
   assert.equal((await(await api.get(`${base}/api/projects/${pid}/workspace?include_chat=false`)).json()).workspace.items.length,0);
   await page.locator('#ch-sidebar-collapse').click();assert.equal(await page.locator('.sidebar').isVisible(),false);await page.locator('#ch-menu').click();
   const common=await page.locator('.ch-current-materials').boundingBox(),history=await page.locator('.ch-history-scroll').boundingBox();assert(common.y+common.height<=history.y+1);
   await screenshot('conversation-desktop');
  }
  const project=(await(await api.post(base+'/api/conversations/start',{data:{request_id:'ui-long-'+Date.now(),title:'面向非独立同分布与客户端异构的个性化联邦表征学习与采样策略——历史长名称保持不改写',conversation_title:'在已有文献基础上讨论如何收敛公平比较条件并审阅资源有限的研究方案',domain:'ai',guidance_mode:'direct',entry_stage:'design'}})).json());
  await page.goto(`${base}/workspace?page=dashboard&project=${project.project_id}&conversation=${project.conversation_id}`);await ready();await screenshot('long-name-desktop');
  await page.setViewportSize({width:1100,height:720});await screenshot('laptop');
  if(!before){
   const input=await page.locator('[data-ref="form"]').boundingBox();assert(input.y+input.height<=720);
   await page.getByLabel('项目与对话操作',{exact:true}).click();assert.match(await page.locator('[data-ref="fullName"]').innerText(),/面向非独立同分布/);await page.keyboard.press('Escape');
   await page.locator('[data-ref="input"]').fill('保留当前会话的草稿');await page.reload();await ready();assert.equal(await page.locator('[data-ref="input"]').inputValue(),'保留当前会话的草稿');
  }
  await page.setViewportSize({width:390,height:844});await screenshot('long-name-mobile');
  await page.locator('#ch-menu').click();await screenshot('navigation-mobile');await page.locator('#ch-menu').click();
  await page.goto(`${base}/workspace?page=papers&project=${project.project_id}&conversation=${project.conversation_id}`);
  await page.getByRole('heading',{name:'文献库',exact:true}).waitFor();await screenshot('papers-mobile');
  if(!before){
   assert.equal(await page.locator('#sidebar-notes').isVisible(),false);
   const stages=page.locator('.ui-flow-nav');await stages.locator('> summary').click();assert.equal(await stages.locator('[data-stage]').count(),4);assert.match(await stages.innerText(),/明确条件/);assert.doesNotMatch(await stages.innerText(),/实验执行|成果写作/);await stages.locator('> summary').click();
   const upload=page.locator('.paper-import');await upload.locator('> summary').click();
   await upload.locator('input[type="file"]').setInputFiles({name:'ui-source.md',mimeType:'text/markdown',buffer:Buffer.from('# UI verification\nActual uploaded content; not a research finding.')});
   await upload.locator('input[type="text"]').fill('界面验收资料');await upload.getByRole('button',{name:'上传文献',exact:true}).click();
   const row=page.locator('.list-item[data-paper-id]').filter({hasText:'界面验收资料'});await row.waitFor();assert.match(await row.innerText(),/阅读范围/);await row.getByRole('button',{name:'查看资料：界面验收资料',exact:true}).focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('#badge-papers').innerText(),'1');
   assert.equal(await row.getByRole('button',{name:'查看资料：界面验收资料',exact:true}).getAttribute('aria-expanded'),'true');
   assert.match(await row.innerText(),/正文开头节选/);assert.equal(await row.locator('.paper-technical').evaluate(n=>n.open),false);
   assert.equal(await row.locator('.js-pdf-download,.btn-pdf-download').count(),0);
   assert.doesNotMatch(await page.locator('#content').innerText(),/Sciverse 10 \+ arXiv 3|doc_id|offset|Paper ID/);
   await screenshot('paper-reading-mobile');
   await page.setViewportSize({width:1440,height:900});await screenshot('papers-desktop');await page.setViewportSize({width:390,height:844});
   await page.getByPlaceholder('搜索标题、作者或摘要…').fill('不存在的资料');await page.getByText(/^没有符合筛选条件的论文，/).waitFor();
   await page.goto(`${base}/workspace?page=notes&project=${project.project_id}&conversation=${project.conversation_id}`);await page.locator('#note-input').fill('界面验收：保留待讨论的想法，不代表结论。');await page.getByRole('button',{name:'保存笔记',exact:true}).click();await page.locator('#content').getByText('界面验收：保留待讨论的想法，不代表结论。',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('#badge-notes').textContent==='1');
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,before,screenshots:shots,project_id:project.project_id,conversation_id:project.conversation_id,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
