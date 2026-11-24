/* 使用隔离测试服务的真实 API / SQLite；不拦截接口、不执行实验或调用模型。 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[], forbidden=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(/\/(run|run-topic-discovery|human-response|guide|backfill)(\?|$)/.test(r.url()))forbidden.push(r.url());});
    const base='http://127.0.0.1:8012';
    const created=await page.request.post(base+'/api/projects',{data:{topic:'软件测试：研究记录衔接，不是科研成果',domain:'ai'}});
    assert.equal(created.status(),200);
    const pid=(await created.json()).project_id;
    const flow=()=>page.getByRole('region',{name:'研究主线',exact:true});
    const record=page.getByRole('textbox',{name:'记录内容',exact:true});
    async function save(title,content='软件测试说明，不是真实研究结果。') {
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(title);
        await record.fill(content);
        await page.getByRole('button',{name:'保存记录',exact:true}).click();
        await page.locator('.research-record > summary').filter({hasText:title}).waitFor();
    }
    async function snapshot() {return (await page.request.get(`${base}/api/projects/${pid}/workspace`)).json();}
    try {
        await page.goto(`${base}/workspace?project=${pid}&page=dashboard`);
        await flow().getByRole('button',{name:'补充研究问题',exact:true}).click();
        await save('问题A：测试条件下的比较');
        await flow().getByRole('button',{name:'补充研究方案',exact:true}).click();
        assert.ok(await page.getByRole('combobox',{name:'关联记录',exact:true}).inputValue(),'方案应已关联问题');
        await save('方案A：固定条件的对照');
        await flow().getByRole('button',{name:'补充实际结果',exact:true}).click();
        assert.ok(await page.getByRole('combobox',{name:'关联记录',exact:true}).inputValue(),'结果应已关联方案');
        await page.getByRole('combobox',{name:'运行状态',exact:true}).selectOption('failed');
        await save('结果A：测试运行失败','测试夹具：运行失败，没有实际指标。');
        await flow().getByRole('button',{name:'补充研究判断',exact:true}).click();
        assert.equal(await page.getByRole('combobox',{name:'支持程度',exact:true}).inputValue(),'inconclusive');
        await save('判断A：证据不足');
        await flow().getByText('用户判断：无法定论',{exact:true}).waitFor();
        assert.match(await flow().innerText(),/不代表创新成立或论文已完成/);
        const before=await snapshot();
        assert.equal(before.workspace.items.length,4);
        assert.equal(before.workspace.items.filter(i=>i.kind==='result')[0].result_status,'failed');
        assert.deepEqual(before.workspace.items.filter(i=>i.kind==='result')[0].metrics,{});

        // 关联链接应定位到问题原记录；修订不得覆盖旧记录。
        await flow().getByRole('button',{name:'研究问题：问题A：测试条件下的比较',exact:true}).click();
        const questionDetails=page.locator('.research-record').filter({has:page.locator('summary',{hasText:'问题A：测试条件下的比较'})});
        assert.equal(await questionDetails.getAttribute('open'),'');
        await questionDetails.getByRole('button',{name:'修订这条记录',exact:true}).click();
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill('问题A：收窄条件后的新版');
        await record.fill('软件测试：修改研究条件后必须复核旧方案。');
        await page.getByRole('checkbox',{name:'已核对修订依据',exact:true}).check();
        await page.getByRole('button',{name:'保存修订版本',exact:true}).click();
        await flow().getByRole('button',{name:'补充研究方案',exact:true}).waitFor();
        const revised=await snapshot();
        assert.equal(revised.workspace.items.length,5);
        assert.equal(revised.workspace.items[0].status,'superseded');
        assert.equal(revised.workspace.items.filter(i=>i.needs_review).length,3);
        await flow().getByText('待复核记录 3 条（未计作已衔接的工作）',{exact:true}).waitFor();
        await page.reload();
        assert.equal(await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).inputValue(),revised.workspace.items.at(-1).item_id);
        assert.match(await flow().innerText(),/尚无关联方案/);

        // 添加第二问题，必须单独推进，不能借用 A 的结果。
        const second=await page.request.post(`${base}/api/projects/${pid}/workspace/items`,{data:{expected_revision:revised.workspace.revision,item:{kind:'question',title:'问题B：独立测试问题',content:'只作为测试记录，不与A共享研究结论。'}}});
        assert.equal(second.status(),200);
        const b=(await second.json()).workspace.items.at(-1);
        await page.reload();
        await flow().getByRole('combobox',{name:'当前研究问题',exact:true}).selectOption(b.item_id);
        await flow().getByRole('button',{name:'补充研究方案',exact:true}).waitFor();
        assert.equal(await flow().getByRole('button',{name:/结果A/}).count(),0);
        await flow().getByRole('button',{name:'讨论当前问题',exact:true}).click();
        await page.locator('#rg-focus').filter({hasText:b.title}).waitFor();
        assert.match(await page.locator('#rg-input').inputValue(),/问题B/);
        assert.deepEqual(forbidden,[]);
        assert.deepEqual(errors,[]);
        console.log(JSON.stringify({real_api_and_sqlite:true,question_plan_result_finding:true,failed_run_inconclusive:true,revision_preserves_history:true,stale_dependents:3,focus_persisted:true,no_cross_question_reuse:true,guide_context:true,errors,forbidden}));
    } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
