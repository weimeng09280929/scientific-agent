/* 本地浏览器联调：不启动模型/流水线；写入明确标注的软件测试记录。 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
    const base = process.env.SRA_BROWSER_TEST_BASE || 'http://127.0.0.1:8001';
    const browser = await chromium.launch({headless:true, channel:'msedge'});
    const context = await browser.newContext({viewport:{width:1600,height:1000}});
    const page = await context.newPage();
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const failedRequests=[];
    page.on('response',r=>{if(r.status()>=400 && r.url().includes('/api/')) failedRequests.push({status:r.status(),url:r.url()});});
    // 禁止本次 UI 测试意外调用付费模型或启动真实科研任务。
    await page.route(/\/api\/projects\/[^/]+\/(?:run|run-topic-discovery|workspace\/guide|regenerate-method-doc)(?:\?|$)/, route=>route.abort());
    try {
        const response=await context.request.get(`${base}/api/projects`);
        assert(response.ok());
        const projects=(await response.json()).projects;
        let project=projects.find(p=>p.project_id==='proj_20261016_141208_b12225');
        if(!project) {
            const created=await context.request.post(`${base}/api/projects`,{data:{topic:'联邦学习 · 导航联调（软件测试，无实际实验）',domain:'ai'}});
            assert(created.ok());project=await created.json();
        }
        const pid=project.project_id;
        await page.goto(`${base}/workspace?project=${encodeURIComponent(pid)}&page=gaps`);
        await page.getByRole('heading',{name:'研究问题',exact:true}).waitFor();
        await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
        await page.locator('#topbar-status-text').filter({hasText:'已创建'}).waitFor();
        const savedState=await context.request.get(`${base}/api/projects/${pid}/workspace`);
        assert.equal(await page.locator('#badge-gaps').textContent(),String((await savedState.json()).counts.question));
        const nav=[['dashboard','项目总览'],['create','新建与管理'],['topic-discovery','选题探索'],['papers','文献库'],['research-report','研究现状'],['gaps','研究问题'],['experiments','方案与实验'],['writing','成果写作'],['progress','任务进度'],['notes','灵感笔记'],['human','待确认事项'],['methods','研究指南']];
        for(const [id,title] of nav) {
            await page.locator(`#nav [data-page="${id}"]`).click();
            await page.locator('#topbar-title').filter({hasText:title}).waitFor();
            await page.getByRole('heading',{name:title,exact:true}).waitFor();
            assert.equal(await page.locator('#nav [aria-current="page"]').getAttribute('data-page'),id);
        }
        await page.getByRole('tab',{name:'论文写作',exact:true}).click();
        await page.getByRole('heading',{name:'论文写作方法论',exact:true}).waitFor();
        await page.getByRole('tab',{name:'论文阅读',exact:true}).click();
        await page.getByRole('heading',{name:'论文阅读方法论',exact:true}).waitFor();
        await page.locator('#nav [data-page="gaps"]').click();
        await page.getByRole('tab',{name:'文献中的待核实问题',exact:true}).click();
        await page.getByRole('heading',{name:'文献中的待核实问题',exact:true}).waitFor();
        await page.getByRole('tab',{name:'论断与证据',exact:true}).click();
        await page.getByRole('heading',{name:'论断与证据',exact:true}).waitFor();
        await page.getByRole('tab',{name:'我的研究问题',exact:true}).click();
        await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
        const marker=`【导航联调 ${Date.now()}】`;
        await page.getByText('手动新增研究记录',{exact:true}).click();
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(`${marker}研究问题`);
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill('软件测试用的待研究问题：核查文献是否覆盖当前场景。没有检索或实验，不作创新及效果判断。');
        await page.getByRole('button',{name:'保存记录',exact:true}).click();
        await page.locator('#content').getByText(`研究问题 · ${marker}研究问题`,{exact:true}).waitFor();
        await page.reload();
        await page.locator('#content').getByText(`研究问题 · ${marker}研究问题`,{exact:true}).waitFor();
        await page.locator('#nav [data-page="experiments"]').click();
        await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
        await page.getByText('手动新增研究记录',{exact:true}).click();
        await page.getByRole('combobox',{name:'记录类型',exact:true}).selectOption('plan');
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(`${marker}方案草案`);
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill('软件测试用方案：先补查文献，之后再确定数据和基线。本记录没有执行实验或填写预期指标。');
        const reference=page.getByRole('combobox',{name:'关联记录',exact:true});
        await reference.selectOption({label:`研究问题：${marker}研究问题`});
        await page.getByRole('button',{name:'保存记录',exact:true}).click();
        await page.locator('#content').getByText(`研究方案 · ${marker}方案草案`,{exact:true}).waitFor();
        await page.locator('#nav [data-page="writing"]').click();
        await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
        await page.getByText('手动新增研究记录',{exact:true}).click();
        await page.getByRole('textbox',{name:'记录标题',exact:true}).fill(`${marker}写作提纲`);
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill('背景：待补文献。问题：待核查。方法：仅有草案。实验结果：没有运行，待补充。局限：本条仅用于界面联调，不是科研成果。');
        await page.getByRole('button',{name:'保存记录',exact:true}).click();
        await page.locator('#content').getByText(`写作提纲 · ${marker}写作提纲`,{exact:true}).waitFor();
        const exported=await context.request.get(`${base}/api/projects/${pid}/workspace/export?format=md`);
        assert(exported.ok());assert((await exported.text()).includes(marker));
        // 历史深链保留；选中的仍应是合并后的菜单。
        for(const [old,owner,title] of [['claims','gaps','研究问题 · 论断与证据'],['reading-method','methods','研究指南 · 论文阅读'],['writing-method','methods','研究指南 · 论文写作']]) {
            await page.goto(`${base}/workspace?project=${pid}&page=${old}`);
            await page.locator('#topbar-title').filter({hasText:title}).waitFor();
            assert.equal(await page.locator('#nav [aria-current="page"]').getAttribute('data-page'),owner);
        }
        await page.goto(`${base}/workspace?project=${pid}&page=gaps`);
        await page.getByRole('button',{name:'刷新记录',exact:true}).waitFor();
        // 输入问号不能再劫持输入焦点或弹出帮助。
        await page.getByText('手动新增研究记录',{exact:true}).click();
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill('这条问题的证据是什么');
        await page.getByRole('textbox',{name:'记录内容',exact:true}).press('?');
        assert.equal(await page.locator('#help-panel').isVisible(),false);
        await page.getByRole('textbox',{name:'记录内容',exact:true}).fill('');
        await page.getByText('手动新增研究记录',{exact:true}).click();
        if(await page.locator('#research-guide-toggle').getAttribute('aria-expanded')!=='true') await page.locator('#research-guide-toggle').click();
        await page.locator('#rg-page').filter({hasText:'研究问题'}).waitFor();
        const screenshot=path.join(__dirname,'../docs/research-navigation-preview.png');
        await page.screenshot({path:screenshot,fullPage:false});
        await page.setViewportSize({width:375,height:812});
        if(await page.locator('#research-guide-toggle').getAttribute('aria-expanded')==='true') await page.locator('#research-guide-toggle').click();
        const widths=await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
        assert(widths.content<=widths.viewport,JSON.stringify(widths));
        assert.deepEqual(errors,[]);
        assert.deepEqual(failedRequests,[]);
        console.log(JSON.stringify({project_id:pid,pages:nav.length,records_saved:3,legacy_links:3,errors,failedRequests,mobile:widths,screenshot},null,2));
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
