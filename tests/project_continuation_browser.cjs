/* Real temporary API/SQLite on 8012, dry-run only; never accesses user projects. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const base='http://127.0.0.1:8012', errors=[];
    let pid;
    page.on('pageerror',e=>errors.push(e.message));
    const status=async()=> (await page.request.get(`${base}/api/projects/${pid}/status`)).json();
    try {
        await page.goto(base+'/workspace');
        await page.getByRole('region',{name:'科研工作台使用说明'}).waitFor();
        assert.match(await page.getByRole('region',{name:'科研工作台使用说明'}).innerText(),/系统不代跑实验/);
        const create=async topic=>(await (await page.request.post(base+'/api/projects',{data:{topic,domain:'ai'}})).json()).project_id;
        pid=await create('恢复测试A（软件夹具）');
        const other=await create('切换测试B（软件夹具）');
        await page.goto(`${base}/workspace?project=${other}&page=create`);
        await page.locator('#resume-project-input').fill(pid);
        await page.locator('#resume-project-btn').click();
        await page.waitForFunction(pid=>document.getElementById('current-project-id')?.textContent===pid,pid);
        await page.waitForFunction(()=>document.querySelector('.nav-item.active')?.dataset.page==='progress');
        // Poll the awaited API response in Node; an async browser predicate can
        // return a truthy Promise handle before the transition is observable.
        let before;
        for(let attempt=0;attempt<100;attempt++) {
            before=await status();
            if(before.status==='pending_human' && before.pending_human?.context?.node_id==='topic_confirm') break;
            await page.waitForTimeout(100);
        }
        assert.ok(before.pending_human,JSON.stringify({pid,actual:before.project_id,status:before.status,next:before.continuation,url:page.url()}));
        assert.equal(before.pending_human.context.node_id,'topic_confirm');
        assert.equal(await page.evaluate(()=>localStorage.getItem('sra_project_id')),pid);
        assert.equal(new URL(page.url()).searchParams.get('project'),pid);
        assert.equal((await page.request.post(`${base}/api/projects/${pid}/resume`)).status(),200);
        const unchanged=await status();
        assert.equal(unchanged.pending_human.appeared_at,before.pending_human.appeared_at);
        assert.deepEqual(unchanged.node_history,before.node_history);
        await page.reload();
        await page.locator('#current-project-id').filter({hasText:pid}).waitFor();
        await page.locator('.continuation-card').getByRole('button',{name:'前往确认',exact:true}).click();
        await page.waitForFunction(()=>document.querySelector('.nav-item.active')?.dataset.page==='human');
        await page.locator('.nav-item[data-page="dashboard"]').click();
        await page.getByRole('region',{name:'科研工作台使用说明'}).waitFor();
        assert.equal((await status()).status,'pending_human');
        assert.deepEqual(errors,[]);
        console.log(JSON.stringify({paste_resumes:true,project_id_persisted:true,refresh_preserves_project:true,pending_not_restarted:true,confirmation_route:true,guide_with_and_without_project:true,errors}));
    } finally {
        if(pid) await page.request.post(`${base}/api/projects/${pid}/human-response`,{data:{action:'abort'}});
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exitCode=1;});
