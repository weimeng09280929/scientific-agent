/* Opt-in retained experience, using the real main app and its normal APIs.
   No response fixtures. Run each turn deliberately after reading the prior one. */
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8002',experience='guided-live-20261023-01';
const out=path.resolve('docs/real-guided-user-experience');
(async()=>{
 if(!process.argv.includes('--allow-live'))throw Error('Explicit live authorization flag required');
 const command=process.argv[2],value=process.argv[3]||'';
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 const get=async p=>{const r=await page.request.get(base+p);if(!r.ok())throw Error(r.status()+':'+await r.text());return r.json();};
 const write=async(method,p,data)=>{const r=await page.request[method](base+p,{data,timeout:30000});if(!r.ok())throw Error(r.status()+':'+await r.text());return r.json();};
 let id;
 try{
  id=await write('post','/api/conversations/start',{request_id:experience,title:'真实体验验收：联邦学习入门',domain:'ai',conversation_title:'从第一次科研到研究方案',guidance_mode:'beginner',entry_stage:'orientation',discussion_step:'conditions',discussion_goal:'新手四周、普通笔记本CPU，理解论文依据并形成可与导师讨论的研究方案；不执行实验。'});
  const root='/api/projects/'+id.project_id,conv=root+'/conversations/'+id.conversation_id;
  const allAudit=async()=>{const entries=[];let offset=0;for(let i=0;i<10;i++){const a=await get(root+'/call-audit?limit=100&offset='+offset);entries.push(...a.entries);if(a.next_offset===null)return entries;offset=a.next_offset;}throw Error('Audit exceeds bounded export pagination');};
  if(['send','plan','revise','search','retry'].includes(command)){
   const recorded=(await allAudit()).filter(e=>e.event==='network');
   if(recorded.filter(e=>e.kind==='model').length>=16)throw Error('This completed experience has reached its 16-attempt allowance; no generation is resubmitted');
   if(command==='search'&&recorded.filter(e=>e.kind==='source').length>=6)throw Error('Academic source allowance exhausted');
  }
  await page.goto(base+'/workspace?page=dashboard&project='+id.project_id+'&conversation='+id.conversation_id);await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
  const paperArg=process.argv.find(s=>s.startsWith('--papers='));
  if(paperArg){
   await page.locator('[data-ref="panelToggle"]').click();await page.getByRole('tab',{name:'文献与报告',exact:true}).click();
   for(const pid of paperArg.slice(9).split(',')){const card=page.locator('.ch-paper-card[data-paper-id="'+pid+'"]');if(!await card.evaluate(n=>n.open))await card.locator('>summary').click();await card.getByRole('checkbox').check();}
   await page.locator('[data-ref="panelClose"]').click();
  }
  const rows=async()=>(await get(conv+'/messages?tail=true&limit=100')).messages;
  const waitRequest=async rid=>{let view;for(let i=0;i<240;i++){
   const r=await page.request.get(base+conv+'/requests/'+encodeURIComponent(rid));
   if(r.status()===404){const list=await get(conv+'/research-tasks?limit=10');view=list.tasks.find(t=>t.request_id===rid);if(view&&!['queued','running'].includes(view.status))return view;}
   else {if(!r.ok())throw Error(await r.text());view=await r.json();if(view.status!=='processing')return view;}
   await page.waitForTimeout(1000);}return view;};
  let result;
  if(command==='init')result={identity:id,url:page.url(),capabilities:await get('/api/conversation-capabilities')};
  else if(command==='send'){
   const prior=await rows(),seq=new Set(prior.map(m=>m.message_id));
   await page.locator('#ch-input').fill(value);await page.locator('[data-ref="send"]').click();
   let m;for(let i=0;i<100;i++){m=(await rows()).find(m=>m.role==='user'&&!seq.has(m.message_id)&&m.content===value);if(m)break;await page.waitForTimeout(100);}
   if(!m)throw Error('User message was not persisted; no automatic resend');
   result=await waitRequest(m.request_id);
  }else if(command==='step'){
   const d=page.locator('.ch-route-details');if(!await d.evaluate(n=>n.open))await d.locator('>summary').click();await page.locator('[data-research-step="'+value+'"]').click();
   await page.waitForFunction(s=>document.querySelector('[data-research-step="'+s+'"]')?.getAttribute('aria-pressed')==='true',value);result=await get(conv+'/research-route');
  }else if(command==='inspect'){
   result={messages:await rows(),workspace:await get(root+'/workspace?include_chat=false'),suggestions:await get(conv+'/suggestions?limit=30&recent=true'),tasks:await get(conv+'/research-tasks?limit=10')};
  }else if(command==='retry'){
   const previous=(await rows()).find(m=>m.role==='user'&&m.request_id===value&&m.request_status==='failed');if(!previous)throw Error('No failed original user request to retry');
   const before=await get(conv+'/requests/'+encodeURIComponent(value));const card=page.locator('.ch-message-user').filter({hasText:previous.content});await card.getByRole('button',{name:'重试此消息',exact:true}).click();
   let claimed=false;for(let i=0;i<100;i++){const r=await get(conv+'/requests/'+encodeURIComponent(value));if(r.attempt>before.attempt){claimed=true;break;}await page.waitForTimeout(100);}
   if(!claimed)throw Error('Original retry has not been claimed; inspect status before any further click');result=await waitRequest(value);
  }else if(command==='brief'){
   const w=await get(root+'/workspace?include_chat=false');result=await write('put',root+'/workspace/brief',{expected_revision:w.workspace.revision,brief:JSON.parse(value)});
  }else if(command==='search'){
   // The real UI confirms scope. No injected source lists or fabricated papers.
   const d=page.locator('.ch-route-details');if(!await d.evaluate(n=>n.open))await d.locator('>summary').click();await page.locator('[data-research-step="reading"]').click();
   await page.locator('[data-ref="beginResearchStep"]').filter({hasText:'检索论文并存入文献库'}).click();await page.getByRole('textbox',{name:'检索主题',exact:true}).fill(value);await page.getByRole('button',{name:'开始检索',exact:true}).click();
   let task;for(let i=0;i<260;i++){const list=await get(conv+'/research-tasks?limit=10');task=list.tasks[0];if(task&&!['queued','running'].includes(task.status))break;await page.waitForTimeout(1000);}result=task;
  }else if(command==='adopt'){
   const data=await get(conv+'/suggestions?limit=30&recent=true'),s=data.suggestions.find(s=>s.suggestion_id===value);if(!s)throw Error('No actual suggestion for adoption');
   if(s.kind==='question')await page.getByRole('button',{name:'采用这个问题',exact:true}).last().click();
   else if(s.kind==='plan'){await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();await page.locator('.ch-plan-review[open]').last().getByRole('button',{name:'采纳方案',exact:true}).click();}
   else throw Error('This experience only adopts questions and plans');
   for(let i=0;i<60;i++){const fresh=await get(conv+'/suggestions?limit=30&recent=true');const applied=fresh.suggestions.find(x=>x.suggestion_id===value);if(applied?.applied_item_id){result={suggestion_id:value,item_id:applied.applied_item_id,kind:s.kind,adopted:true};break;}await page.waitForTimeout(100);}
   if(!result)throw Error('Adoption not confirmed; no duplicate request sent');
  }else if(command==='plan'||command==='revise'){
   if(command==='revise'){
    await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();await page.locator('.ch-plan-review[open]').last().getByRole('button',{name:'请求修订方案',exact:true}).click();
    if(await page.locator('[data-ref="panelClose"]').isVisible())await page.locator('[data-ref="panelClose"]').click();
   }else{const d=page.locator('.ch-plan-tools');if(!await d.evaluate(n=>n.open))await d.locator('>summary').click();await page.getByRole('combobox',{name:'方案研究方法',exact:true}).selectOption('computational');await page.getByRole('button',{name:'准备生成方案请求',exact:true}).click();}
   if(value)await page.locator('#ch-input').fill(value);
   const prior=await rows(),seq=new Set(prior.map(m=>m.message_id));await page.locator('[data-ref="send"]').click();let m;
   for(let i=0;i<100;i++){m=(await rows()).find(m=>m.role==='user'&&!seq.has(m.message_id));if(m)break;await page.waitForTimeout(100);}
   if(!m)throw Error('No plan request saved');result=await waitRequest(m.request_id);
  }else if(command==='export'){
   const w=await get(root+'/workspace?include_chat=false');if(!w.workflow.flow.planReady)throw Error('No valid adopted plan to export');
   const item=w.workflow.flow.plan;await page.locator('[data-ref="downloadToggle"]').click();const row=page.locator('[data-export-key="plan:'+item.item_id+'"]');await row.waitFor();const download=page.waitForEvent('download');await row.getByRole('link',{name:'下载',exact:true}).click();fs.mkdirSync(out,{recursive:true});await(await download).saveAs(path.join(out,'adopted-research-plan.md'));result={exported:true,item_id:item.item_id,planReady:true};
  }else if(command==='archive'){
   const w=await get(root+'/workspace?include_chat=false');if(!w.workflow.flow.planReady)throw Error('No adopted valid final plan');
   const record=w.workflow.flow.plan;
   const folder=path.resolve('projects',id.project_id,'artifacts','guided-experience-20261023');fs.mkdirSync(folder,{recursive:true});fs.mkdirSync(out,{recursive:true});
   const save=(name,body)=>{fs.writeFileSync(path.join(folder,name),body);fs.writeFileSync(path.join(out,name),body);};
   const plan=await page.request.get(base+root+'/exports/items/'+record.item_id);if(!plan.ok())throw Error('Plan export failed');save('adopted-research-plan.md',await plan.body());
   for(const item of w.workspace.items.filter(i=>i.kind==='plan'||i.kind==='question')){const r=await page.request.get(base+root+'/exports/items/'+item.item_id);if(!r.ok())throw Error('Historical record export failed');save(item.kind==='question'?'confirmed-research-question.md':'research-plan-v'+item.plan_version+'.md',await r.body());}
   const history=await rows();save('conversation-transcript.md','# 真实模型逐轮体验记录\n\n这是独立验收项目的模拟用户条件，不代表真实用户背景；没有执行实验。\n\n'+history.map(m=>'## '+(m.role==='user'?'体验用户':'科研指导')+' · '+m.created_at+'\n\n'+m.content+(m.delivery_warnings?.length?'\n\n> 操作提示：'+m.delivery_warnings.join('；'):'')+'\n').join('\n'));
   save('public-source-catalog.md','# 项目实际入库资料\n\n仅元信息与摘要；未将其标记为已阅读全文。\n\n'+w.papers.map((p,i)=>`${i+1}. ${p.title}（${p.year||'年份待核'}）\n   - 作者：${p.authors.join('、')}\n   - 来源：${p.url}\n   - 阅读范围：${p.reading_scope||p.scope}\n`).join('\n'));
   const tasks=await get(conv+'/research-tasks?limit=10');for(const t of tasks.tasks){const r=await page.request.get(base+root+'/research-tasks/'+t.task_id+'/export');if(r.ok())save('research-status-report.md',await r.body());}
   const audit=await allAudit(),net=audit.filter(e=>e.event==='network'),models=net.filter(e=>e.kind==='model');
   const manifest={project_id:id.project_id,conversation_id:id.conversation_id,url:page.url(),title:w.project.display_name,planReady:w.workflow.flow.planReady,formal_plan_id:record.item_id,plan_version:record.plan_version,records:w.workspace.items.map(i=>({item_id:i.item_id,kind:i.kind,status:i.status,supersedes_id:i.supersedes_id,plan_version:i.plan_version})),papers:w.papers.length,messages:history.length,user_turns:history.filter(m=>m.role==='user').length,model_requests:models.length,source_requests:net.filter(e=>e.kind==='source').length,prompt_tokens:models.reduce((s,e)=>s+(e.usage?.prompt_tokens||0),0),completion_tokens:models.reduce((s,e)=>s+(e.usage?.completion_tokens||0),0),unknown_usage_requests:models.filter(e=>!e.usage).length,cost:null,cost_basis:'unknown; no verified gateway tariff or bill',artifact_folder:folder,exported_at:new Date().toISOString()};
   save('call-audit.json',JSON.stringify(audit,null,2));save('experience-manifest.json',JSON.stringify(manifest,null,2));result=manifest;
   await page.locator('.ch-messages').getByRole('button',{name:'打开方案',exact:true}).last().click();await page.locator('.ch-material-panel .ch-plan-review[open]').last().waitFor({state:'visible'});await page.screenshot({path:path.join(out,'final-plan-desktop.png')});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'final-plan-mobile.png')});
  }else throw Error('Unknown command');
  fs.mkdirSync(out,{recursive:true});
  if(command!=='init'&&command!=='archive'){await page.reload();await page.locator('[data-ref="send"]').waitFor();await page.locator('[data-ref="messages"]').evaluate(n=>{n.scrollTop=n.scrollHeight;});}
  if(command!=='archive')await page.screenshot({path:path.join(out,command+'-desktop.png')});
  const network=(await allAudit()).filter(e=>e.event==='network');
  if(result&&typeof result==='object'){
   delete result.call_audit;
   if(command==='step')result={discussion_step:result.discussion_step,next_step:result.next_step,question_id:result.question_id,provided_readable_papers:result.provided_readable_papers};
   if(command==='brief')result={brief:result.workspace?.brief,revision:result.workspace?.revision};
   if((command==='plan'||command==='revise')&&result.suggestions)result.suggestions=result.suggestions.map(s=>({...s,content:s.content.slice(0,400),plan:s.plan?{method:s.plan.method,question_id:s.plan.question_id,conditions:s.plan.conditions,method_details:s.plan.method_details,unconfirmed:s.plan.unconfirmed,sections:s.plan.sections.map(sec=>({key:sec.key,statements:sec.statements.map(st=>st.text)}))}:null}));
   if(result.task_id)result={task_id:result.task_id,status:result.status,stage:result.stage,error:result.error,failed_sources:result.failed_sources,saved_paper_ids:result.saved_paper_ids,report:result.report,queries:result.queries,source_calls:result.source_calls};
  }
  console.log(JSON.stringify({identity:{project_id:id.project_id,conversation_id:id.conversation_id},command,result,audit:{model_requests:network.filter(e=>e.kind==='model').length,source_requests:network.filter(e=>e.kind==='source').length,network:network.map(e=>({kind:e.kind,name:e.name,outcome:e.outcome,elapsed_ms:e.elapsed_ms,usage:e.usage,cost:e.cost,cost_basis:e.cost_basis}))},errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
