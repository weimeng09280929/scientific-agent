/* 真实网页验收：写入操作均经页面按钮触发，不拦截网络或模拟模型响应。 */
const {chromium}=require('playwright');
const fs=require('node:fs');
const [action,pid='',argument='',extra='']=process.argv.slice(2);
const BASE='http://127.0.0.1:8001';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1600,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.setDefaultTimeout(20000);
 const status=async (id=pid)=>(await (await page.request.get(`${BASE}/api/projects/${id}/status`)).json());
 try{
  if(action==='create'){
   await page.goto(`${BASE}/workspace?page=create`);
   await page.locator('#domain-select').selectOption('ai');
   await page.locator('#topic-input').fill(argument);
   const created=page.waitForResponse(r=>r.url().endsWith('/api/projects')&&r.request().method()==='POST');
   await page.locator(extra==='direct'?'#create-btn':'#topic-discovery-create-btn').click();
   const result=await (await created).json();
   console.log(JSON.stringify({created:result,errors}));
  }else if(action==='abort'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=human`);
   await page.locator('#human-submit-abort').click();
   console.log(JSON.stringify({status:(await status()).status,errors}));
  }else if(action==='human'){
   let before=await status();
   for(let attempt=0;before.status==='running'&&attempt<20;attempt++){
    await new Promise(resolve=>setTimeout(resolve,1000));before=await status();
   }
   if(before.status!=='pending_human')throw Error(`不是待确认状态：${before.status}`);
   await page.goto(`${BASE}/workspace?project=${pid}&page=human`);
   await page.locator('#human-response-input').fill(fs.readFileSync(argument,'utf8'));
   await page.locator('#human-submit-continue').click();
   await page.waitForFunction(()=>!document.querySelector('#human-submit-continue')||!document.querySelector('#human-submit-continue').disabled);
   console.log(JSON.stringify({submitted:true,before_node:before.current_node,after:(await status()).status,errors}));
  }else if(action==='guide'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=${extra||'research-report'}`);
   await page.locator('#rg-send').waitFor();
   await page.waitForFunction(()=>!document.querySelector('#rg-send').disabled);
   await page.locator('#rg-input').fill(fs.readFileSync(argument,'utf8'));
   const received=page.waitForResponse(r=>r.url().endsWith('/workspace/guide')&&r.request().method()==='POST',{timeout:240000});
   await page.locator('#rg-send').click();
   const response=await received;
   const result=await response.json();
   console.log(JSON.stringify({status:response.status(),messages:result.workspace?.messages?.slice(-2),detail:result.detail,errors}));
  }else if(action==='adopt-question'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=gaps`);
   await page.waitForFunction(()=>!document.querySelector('#rg-send').disabled);
   const data=await (await page.request.get(`${BASE}/api/projects/${pid}/workspace`)).json();
   const candidate=data.workspace.suggestions.find(item=>item.kind==='question'&&!item.applied_item_id);
   if(!candidate)throw Error('没有待采纳的问题草案');
   const card=page.locator('.rg-suggestion').filter({hasText:candidate.title});
   await card.locator('summary').click();
   const response=page.waitForResponse(r=>r.url().endsWith(`/suggestions/${candidate.suggestion_id}/apply`));
   await card.getByRole('button',{name:'采纳到研究记录',exact:true}).click();
   const result=await (await response).json();
   console.log(JSON.stringify({question:result.workspace?.items?.find(item=>item.item_id===result.workspace.suggestions.find(s=>s.suggestion_id===candidate.suggestion_id)?.applied_item_id),detail:result.detail,errors}));
  }else if(action==='record'){
   const input=JSON.parse(fs.readFileSync(argument,'utf8'));
   const target=input.kind==='question'?'gaps':['plan','result','finding'].includes(input.kind)?'experiments':'writing';
   await page.goto(`${BASE}/workspace?project=${pid}&page=${target}`);
   await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
   await page.getByText('手动新增研究记录',{exact:true}).click();
   await page.getByRole('combobox',{name:'记录类型',exact:true}).selectOption(input.kind);
   await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(input.title);
   await page.getByRole('textbox',{name:'记录内容',exact:true}).fill(input.content);
   if(input.related_id)await page.getByRole('combobox',{name:'关联记录',exact:true}).selectOption(input.related_id);
   else if(input.related_title){
    const selector=page.getByRole('combobox',{name:'关联记录',exact:true});
    const title=input.related_title.slice(input.related_title.indexOf('：')+1);
    const options=await selector.locator('option').evaluateAll(items=>items.map(item=>({value:item.value,text:item.textContent})));
    const match=options.find(option=>option.value&&option.text.endsWith(title));
    if(!match)throw Error(`找不到关联记录：${JSON.stringify(options)}`);
    await selector.selectOption(match.value);
   }
   if(input.kind==='result'){
    await page.getByRole('combobox',{name:'运行状态',exact:true}).selectOption(input.result_status);
    await page.getByRole('textbox',{name:'实际指标',exact:true}).fill(JSON.stringify(input.metrics));
   }
   if(input.kind==='finding')await page.getByRole('combobox',{name:'支持程度',exact:true}).selectOption(input.assessment);
   const response=page.waitForResponse(r=>r.url().endsWith('/workspace/items')&&r.request().method()==='POST');
   await page.getByRole('button',{name:'保存记录',exact:true}).click();
   const received=await response,result=await received.json();
   console.log(JSON.stringify({status:received.status(),item:result.workspace?.items?.at(-1),detail:result.detail,errors}));
  }else if(action==='backfill'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=experiments`);
   await page.getByRole('button',{name:'回填实验结果',exact:true}).click();
   await page.getByRole('textbox',{name:'实验结果 JSON',exact:true}).fill(fs.readFileSync(argument,'utf8'));
   const received=page.waitForResponse(r=>r.url().endsWith('/experiments/backfill')&&r.request().method()==='POST');
   await page.getByRole('button',{name:'提交回填',exact:true}).click();
   const response=await received;console.log(JSON.stringify({status:response.status(),result:await response.json(),errors}));
  }else if(action==='continue'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=progress`);
   await page.getByRole('button',{name:'继续 / 重启',exact:true}).click();
   console.log(JSON.stringify({status:(await status()).status,errors}));
  }else if(action==='retry-discovery'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=topic-discovery`);
   await page.getByRole('button',{name:'继续探索选题',exact:true}).click();
   console.log(JSON.stringify({status:(await status()).status,errors}));
  }else if(action==='inspect'){
   await page.goto(`${BASE}/workspace?project=${pid}&page=${extra||'progress'}`);
   await page.locator('.current-node-card').waitFor();
   const before=await page.locator('[aria-current="step"]').allTextContents();
   await page.locator('.nav-item[data-page="research-report"]').click();
   await page.waitForFunction(()=>document.querySelector('#topbar-title').textContent==='研究现状');
   const during=await page.locator('[aria-current="step"]').allTextContents();
   await page.locator('.nav-item[data-page="progress"]').click();
   await page.locator('.current-node-card').waitFor();
   const after=await page.locator('[aria-current="step"]').allTextContents();
   console.log(JSON.stringify({before,during,after,position:await page.locator('.current-node-card').innerText(),errors}));
   if(argument)await page.screenshot({path:argument,fullPage:true});
  }else if(action==='exports'){
   if(!/^proj_\d{8}_\d{6}_[a-f0-9]+$/.test(pid))throw Error('项目ID不合法');
   const path=require('node:path'),folder=path.resolve('projects',pid,'acceptance_exports');
   fs.mkdirSync(folder,{recursive:true});
   const exported=[];
   for(const type of ['research-report','method-doc','experiment-tasks','experiment-results','paper-draft','full-report']){
    for(const format of (type==='paper-draft'?['md','docx','pdf']:['md'])){
     const response=await page.request.get(`${BASE}/api/projects/${pid}/download/${type}?format=${format}`);
     const bytes=await response.body();
     if(!response.ok()||bytes.length<100)throw Error(`下载失败 ${type}.${format}: ${response.status()}`);
     if(format==='md'&&/# 暂无|尚未生成论文稿/.test(bytes.toString('utf8')))throw Error(`下载仍是占位 ${type}`);
     if(format==='pdf'&&!bytes.subarray(0,4).equals(Buffer.from('%PDF')))throw Error('PDF签名无效');
     if(format==='docx'&&bytes.subarray(0,2).toString()!=='PK')throw Error('DOCX签名无效');
     fs.writeFileSync(path.join(folder,`${type}.${format}`),bytes);
     exported.push({type,format,status:response.status(),bytes:bytes.length});
    }
   }
   for(const format of ['md','json']){
    const response=await page.request.get(`${BASE}/api/projects/${pid}/workspace/export?format=${format}`);
    if(!response.ok())throw Error('研究记录导出失败');
    fs.writeFileSync(path.join(folder,`research-records.${format}`),await response.body());
   }
   console.log(JSON.stringify({folder,exported,errors}));
  }else if(action==='status'){
   const data=await status();
   console.log(JSON.stringify({project_id:pid,status:data.status,current_node:data.current_node,counts:data.counts,summary:data.summary,pending:data.pending_human,stage_statuses:data.stage_statuses,last_nodes:data.node_history?.slice(-3)},null,2));
  }else throw Error('Unknown action');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
