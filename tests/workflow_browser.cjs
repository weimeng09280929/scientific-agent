/* 使用页面内网络夹具验证错误恢复，不写入用户项目，不调用真实模型。 */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const pid='browser-workflow-fixture';
    let runCalls=0,backfillCalls=0;
    const status={project_id:pid,topic:'软件测试夹具（不是科研成果）',status:'experiment_pending',current_stage:'experiment',counts:{papers:0,claims:0,gaps:0,experiments:1},stage_statuses:{research:'done',ideation:'done',design:'done',experiment:'done',writing:'not_started'},node_history:[],pending_human:null,topic_discovery:{}};
    const counts={note:0,question:0,plan:0,result:0,finding:0,outline:0};
    const workspace={project_id:pid,revision:0,brief:{},items:[],messages:[],suggestions:[],updated_at:'2026-10-16T00:00:00Z'};
    await page.route(`**/api/projects/${pid}/**`,async route=>{
        const path=new URL(route.request().url()).pathname.split(`${pid}/`)[1];
        let body;
        if(path==='status') body=status;
        else if(path==='workspace') body={workspace,counts,papers:[],project:{topic:status.topic,domain:'ai'},guide_mode:'rules',starters:{study:[],overview:[]}};
        else if(path==='notes') body={notes:[]};
        else if(path==='research-report') body={report:null};
        else if(path==='experiments') body={experiments:[{experiment_id:'test-exp',name:'软件测试任务',status:'planned',verifies_claim_ids:[],config:{}}]};
        else if(path==='experiment-tasks') body={has_tasks:true,readme:'# 软件测试任务\n本说明不是实际科研产物。',plan:{experiments:[{name:'软件测试任务',experiment_id:'test-exp'}]},export_dir:'fixture'};
        else if(path==='method-doc-status') body={status:'idle'};
        else if(path==='download/method-doc') {await route.fulfill({status:503,contentType:'text/plain',body:'测试中的文档读取故障'});return;}
        else if(path==='experiments/backfill'){
            backfillCalls++;
            const content=JSON.parse(route.request().postDataJSON().results_json);
            if(content.experiments[0].status==='typo') {await route.fulfill({status:422,json:{detail:'第 1 条结果无效：状态无效；本批未保存'}});return;}
            body={updated:['软件测试任务'],unmatched:[],message:'测试夹具：结果已保存，请点击继续复核'};
        }else if(path==='resume') {runCalls++;status.status='running';status.current_stage='writing';body={project_id:pid,action:'started',page:'progress',message:'仅浏览器夹具，不启动真实任务'};}
        else throw new Error(`未预期请求：${path}`);
        await route.fulfill({status:200,json:body});
    });
    try{
        await page.goto(`http://127.0.0.1:8001/workspace?project=${pid}&page=experiments`);
        await page.getByRole('heading',{name:'方案与实验',exact:true}).waitFor();
        await page.getByRole('button',{name:'回填实验结果',exact:true}).click();
        const textarea=page.getByRole('textbox',{name:'实验结果 JSON',exact:true});
        const invalid=JSON.stringify({experiments:[{name:'软件测试任务',status:'typo',metrics:{fixture:0.61}}]});
        await textarea.fill(invalid);
        await page.getByRole('button',{name:'提交回填',exact:true}).click();
        await page.locator('#content').getByText('回填失败：第 1 条结果无效：状态无效；本批未保存',{exact:true}).waitFor();
        assert.equal(await textarea.inputValue(),invalid);
        assert.equal(await page.getByRole('button',{name:'提交回填',exact:true}).isEnabled(),true);
        await textarea.fill(JSON.stringify({experiments:[{name:'软件测试任务',status:'success',metrics:{fixture:0.61}}]}));
        await page.getByRole('button',{name:'提交回填',exact:true}).click();
        await page.getByRole('button',{name:'复核结果并继续',exact:true}).waitFor();
        assert.equal(runCalls,0,'提交结果不能悄悄启动付费模型流程');
        await page.getByRole('button',{name:'查看方法文档',exact:true}).click();
        await page.getByText('读取失败：方法文档暂时无法读取',{exact:true}).waitFor();
        await page.getByRole('button',{name:'复核结果并继续',exact:true}).click();
        await page.locator('#topbar-status-text').filter({hasText:'运行中'}).waitFor();
        assert.equal(runCalls,1);
        assert.equal(backfillCalls,2);
        assert.deepEqual(errors,[]);
        console.log(JSON.stringify({backfillCalls,runCalls,errors,input_preserved_after_failure:true,no_automatic_paid_run:true,download_error_visible:true}));
    }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
