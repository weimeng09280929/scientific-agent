/* 独立临时服务上的真实 API/SQLite；不访问用户项目、不调用模型或执行实验。 */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[],forbidden=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(/\/(run|run-topic-discovery|human-response|guide|backfill)(\?|$)/.test(r.url()))forbidden.push(r.url());});
    const base='http://127.0.0.1:8012';
    const response=await page.request.post(base+'/api/projects',{data:{topic:'项目管理软件测试夹具',domain:'ai'}});
    assert.equal(response.status(),200);
    const pid=(await response.json()).project_id;
    const manager=()=>page.getByRole('region',{name:'项目管理',exact:true});
    const snapshot=async()=> (await page.request.get(`${base}/api/projects/${pid}/workspace`)).json();
    async function expand(){const d=manager().locator('.project-brief-editor');if(await d.getAttribute('open')===null) await manager().getByText('编辑目标与资源',{exact:true}).click();}
    try{
        await page.goto(`${base}/workspace?project=${pid}&page=dashboard`);
        await manager().getByText('科研推进状态',{exact:true}).waitFor();
        await expand();
        await manager().getByRole('textbox',{name:'研究目标',exact:true}).fill('六周内完成范围明确的复现');
        await manager().getByRole('textbox',{name:'已有基础',exact:true}).fill('已有代码与论文');
        await manager().getByRole('textbox',{name:'可用资源',exact:true}).fill('个人电脑，仅CPU');
        await manager().getByRole('textbox',{name:'时间安排',exact:true}).fill('每周八小时，共六周');
        await manager().getByRole('button',{name:'保存项目约束',exact:true}).click();
        await manager().locator('.project-goal').filter({hasText:'六周内完成范围明确的复现'}).waitFor();
        let data=await snapshot();
        assert.equal(data.workspace.brief.resources,'个人电脑，仅CPU');
        const revision=data.workspace.revision;
        await expand();
        await manager().getByRole('button',{name:'保存项目约束',exact:true}).click();
        assert.equal((await snapshot()).workspace.revision,revision,'无变化不产生新版本');

        // 草稿跨页面恢复，且后台轮询不应抹去输入。
        await manager().getByRole('textbox',{name:'研究目标',exact:true}).fill('未保存的目标草稿');
        await page.locator('#nav [data-page="methods"]').click();
        await page.locator('#nav [data-page="progress"]').click();
        await page.waitForTimeout(2300);
        assert.equal(await manager().getByRole('textbox',{name:'研究目标',exact:true}).inputValue(),'未保存的目标草稿');
        await manager().getByRole('button',{name:'取消本次修改',exact:true}).click();
        await manager().locator('.project-goal').filter({hasText:'六周内完成范围明确的复现'}).waitFor();
        await page.locator('#nav [data-page="dashboard"]').click();

        // 另一个写入更新版本，保存应报冲突并保留输入。
        await expand();
        await manager().getByRole('textbox',{name:'时间安排',exact:true}).fill('改为四周');
        data=await snapshot();
        const added=await page.request.post(`${base}/api/projects/${pid}/workspace/items`,{data:{expected_revision:data.workspace.revision,item:{kind:'question',title:'测试问题',content:'仅为版本并发测试，不是科研判断。'}}});
        assert.equal(added.status(),200);
        await manager().getByRole('button',{name:'保存项目约束',exact:true}).click();
        await manager().getByRole('status').filter({hasText:'保存失败'}).waitFor();
        assert.equal(await manager().getByRole('textbox',{name:'时间安排',exact:true}).inputValue(),'改为四周');
        await page.reload();
        await manager().getByRole('checkbox',{name:'确认约束变更需复核',exact:true}).waitFor();
        assert.equal(await manager().getByRole('textbox',{name:'时间安排',exact:true}).inputValue(),'改为四周');
        await manager().getByRole('checkbox',{name:'确认约束变更需复核',exact:true}).check();
        await manager().getByRole('button',{name:'保存项目约束',exact:true}).click();
        await manager().getByText('研究依据待复核',{exact:true}).waitFor();
        data=await snapshot();
        assert.equal(data.workspace.brief.time_budget,'改为四周');
        assert.equal(data.workspace.items[0].needs_review,true);

        // 隐藏建议只是本机偏好，不写项目、不代表完成。
        const before=JSON.stringify(data.workspace);
        await manager().getByRole('button',{name:'暂不提醒',exact:true}).first().click();
        await manager().getByRole('button',{name:'恢复本机隐藏的建议',exact:true}).waitFor();
        assert.equal(JSON.stringify((await snapshot()).workspace),before);
        await manager().getByRole('button',{name:'恢复本机隐藏的建议',exact:true}).click();
        await manager().getByRole('button',{name:'讨论调整',exact:true}).first().click();
        assert.match(await page.locator('#rg-input').inputValue(),/四周/);
        assert.deepEqual(errors,[]);assert.deepEqual(forbidden,[]);
        console.log(JSON.stringify({real_api_and_sqlite:true,brief_persisted:true,no_op_no_revision:true,draft_retained_across_pages_and_poll:true,conflict_preserves_input:true,explicit_review_confirmation:true,hidden_task_not_completion:true,guide_uses_constraints:true,errors,forbidden}));
    }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
