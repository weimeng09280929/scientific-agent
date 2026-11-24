/* Real temporary SQLite/API with --seed-mainline. No training/model calls. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const base='http://127.0.0.1:8012', errors=[], forbidden=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(/\/(run|run-topic-discovery|regenerate-method-doc|guide)(\?|$)/.test(request.url()))forbidden.push(request.url());});
    const all=await (await page.request.get(base+'/api/projects')).json();
    const fixture=all.projects.find(p=>p.topic==='主线衔接软件夹具（不是真实科研）');
    assert.ok(fixture,'Launch tests/research_flow_test_server.py --seed-mainline first');
    const pid=fixture.project_id, url=`${base}/workspace?project=${pid}`;
    const snapshot=async()=> (await (await page.request.get(`${base}/api/projects/${pid}/workspace`)).json());
    const flow=()=>page.getByRole('region',{name:'研究主线',exact:true});
    async function save(title,content) {
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(title);
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill(content);
        await page.getByRole('button',{name:'保存记录',exact:true}).click();
        await page.locator('.research-record > summary').filter({hasText:title}).waitFor();
    }
    async function adopt(kind,id) {
        const material=page.locator(`.source-material[data-source-kind="${kind}"][data-source-id="${id}"]`);
        await material.locator('summary').click();
        page.once('dialog',dialog=>dialog.accept());
        await material.getByRole('button',{name:/确认关联为/}).click();
    }
    try {
        await page.goto(url+'&page=dashboard');
        await page.locator('.continuation-card').getByRole('button',{name:'从已有材料确认问题',exact:true}).click();
        await adopt('idea','fixture-idea');
        await flow().getByText('已保存问题',{exact:true}).waitFor();
        let data=await snapshot();
        assert.equal(data.counts.question,1);assert.equal(data.counts.result,0);
        const q=data.workflow.flow.question;
        await page.locator('#nav [data-page="experiments"]').click();
        await adopt('experiment_plan','fixture-exp');
        await flow().getByText('已关联方案',{exact:true}).waitFor();
        const plan=(await snapshot()).workflow.flow.plan;
        await page.locator('#nav [data-page="dashboard"]').click();
        await page.locator('.continuation-card').getByRole('button',{name:'审阅、修改与导出方案',exact:true}).click();
        await page.locator('.research-record > summary').filter({hasText:plan.title}).waitFor();
        assert.equal(await page.getByRole('combobox',{name:'运行状态',exact:true}).count(),0);
        const kinds=await page.getByRole('combobox',{name:'记录类型',exact:true}).locator('option').evaluateAll(options=>options.map(o=>o.value));
        assert.ok(kinds.every(kind=>['note','question','plan'].includes(kind)));
        data=await snapshot();
        assert.equal(data.workflow.flow.planReady,true);assert.equal(data.workflow.flow.recordsReady,false);
        assert.equal(data.workflow.continuation.action,'plan_ready');
        const beforeRevision=data.workspace.revision;
        for(const kind of ['result','finding','outline']) {
            const rejected=await page.request.post(`${base}/api/projects/${pid}/workspace/items`,{data:{expected_revision:beforeRevision,item:{kind,title:'must not store',content:'software fixture'}}});
            assert.equal(rejected.status(),422);
        }
        assert.equal((await snapshot()).workspace.revision,beforeRevision);
        const experiments=(await (await page.request.get(`${base}/api/projects/${pid}/experiments`)).json()).experiments;
        assert.equal(experiments.find(e=>e.experiment_id==='fixture-exp').status,'planned');
        await page.locator('#nav [data-page="writing"]').click();
        await page.waitForFunction(()=>document.getElementById('rg-send')?.disabled);
        assert.equal(await page.getByRole('button',{name:'保存记录',exact:true}).count(),0);
        data=await snapshot();
        assert.equal(data.counts.result,0);assert.equal(data.counts.finding,0);assert.equal(data.counts.outline,0);
        assert.equal(data.workflow.continuation.action,'plan_ready');
        const additional=await page.request.post(`${base}/api/projects/${pid}/workspace/items`,{data:{expected_revision:data.workspace.revision,item:{kind:'question',title:'另一个测试问题B',content:'不能借用问题A的方案或结果。'}}});
        assert.equal(additional.status(),200);
        const q2=(await additional.json()).workspace.items.at(-1);
        await page.goto(url+'&page=dashboard');
        await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).selectOption(q2.item_id);
        await flow().getByRole('button',{name:'补充研究方案',exact:true}).waitFor();
        assert.equal((await snapshot()).workspace.focus.question,q2.item_id);
        await page.evaluate(()=>localStorage.clear());
        await page.reload();
        await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).waitFor();
        assert.equal(await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).inputValue(),q2.item_id);
        assert.equal((await snapshot()).workflow.flow.plan,null);
        await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).selectOption(q.item_id);
        await flow().getByRole('button',{name:'审阅当前方案',exact:true}).waitFor();
        const views=await Promise.all(['workspace','status','dashboard'].map(async suffix=>(await (await page.request.get(`${base}/api/projects/${pid}/${suffix}`)).json()).workflow));
        for(const key of ['focus','counts','continuation','current_stage']) {
            assert.deepEqual(views[0][key],views[1][key]);
            assert.deepEqual(views[1][key],views[2][key]);
        }
        const exported=await page.request.get(`${base}/api/projects/${pid}/workspace/export`);
        assert.match(await exported.text(),/来源材料/);
        await page.waitForFunction(()=>document.getElementById('badge-experiments')?.textContent==='1');
        assert.match(await page.locator('#badge-experiments').getAttribute('title'),/关联去重/);
        await page.goto(url+'&page=progress');
        await page.locator('.stage-progress').waitFor();
        assert.equal(await page.locator('.stage-step[data-stage="experiment"],.stage-step[data-stage="writing"]').count(),0);
        assert.deepEqual(errors,[]);assert.deepEqual(forbidden,[]);
        console.log(JSON.stringify({existingMaterialsLinked:true,continueOpensPlan:true,postPlanWritesDenied:true,historicalExperimentUnchanged:true,writingReadOnly:true,serverFocusSurvivesLocalStorageClear:true,noCrossQuestionMixing:true,sharedStatus:true,productStagesOnly:true,errors,trainingExecuted:false}));
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
