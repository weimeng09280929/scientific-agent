/* Temporary API on 8012 only. No paid calls or real research projects. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const base='http://127.0.0.1:8012', errors=[], unexpected=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(/\/(run|run-topic-discovery|guide|backfill)(\?|$)/.test(r.url())) unexpected.push(r.url());});
    try {
        const ids=[], prefix=`列表UI测试${Date.now()}-`;
        for(let i=0;i<12;i++) {
            const response=await page.request.post(base+'/api/projects',{data:{topic:`${prefix}${String(i).padStart(2,'0')}`,domain:'ai'}});
            assert.equal(response.status(),200);ids.push((await response.json()).project_id);
        }
        await page.goto(`${base}/workspace?project=${ids[0]}&page=create`);
        const library=page.getByRole('region',{name:'已有项目',exact:true});
        await library.locator('.project-library-row').first().waitFor();
        assert.equal(await page.locator('#content input[type="file"]').count(),0);
        assert.equal(await page.locator('#domain-select option').count(),3);
        assert.equal(await page.locator('#resume-project-btn').count(),1);
        await page.getByRole('searchbox',{name:'搜索已有项目'}).fill(prefix);
        assert.equal(await library.locator('.project-library-row').count(),8);
        await library.getByRole('button',{name:'下一页',exact:true}).click();
        assert.equal(await library.locator('.project-library-row').count(),4);
        await library.getByRole('checkbox',{name:'全选当前页',exact:true}).check();
        assert.match(await library.getByRole('status').innerText(),/已选 4 个/);
        await library.getByRole('button',{name:'上一页',exact:true}).click();
        assert.match(await library.getByRole('status').innerText(),/已选 4 个/);
        await library.getByRole('button',{name:'清空选择',exact:true}).click();
        await page.getByRole('searchbox',{name:'搜索已有项目'}).fill(ids[0]);
        assert.equal(await library.locator('.project-library-row').count(),1);
        await page.getByRole('checkbox',{name:`选择项目 ${ids[0]}`,exact:true}).check();
        page.once('dialog',d=>d.dismiss());
        await page.locator('#delete-selected-projects').click();
        assert.equal((await page.request.get(`${base}/api/projects/${ids[0]}/status`)).status(),200);
        assert.equal(await page.locator('#current-project-id').textContent(),ids[0]);
        page.once('dialog',d=>d.accept());
        await page.locator('#delete-selected-projects').click();
        await library.getByText('没有匹配的项目，试试其他关键词。',{exact:true}).waitFor();
        assert.equal((await page.request.get(`${base}/api/projects/${ids[0]}/status`)).status(),404);
        assert.equal(await page.evaluate(()=>localStorage.getItem('sra_project_id')),null);
        assert.equal(new URL(page.url()).searchParams.get('project'),null);
        await page.getByRole('searchbox',{name:'搜索已有项目'}).fill(prefix);
        await library.getByRole('checkbox',{name:'全选当前页',exact:true}).check();
        page.once('dialog',d=>d.accept());
        await page.locator('#delete-selected-projects').click();
        await library.getByRole('status').filter({hasText:'匹配 3 个'}).waitFor();
        await page.screenshot({path:'docs/project-library-compact-preview.png',fullPage:true});
        const remaining=(await (await page.request.get(base+'/api/projects')).json()).projects.find(p=>ids.includes(p.project_id));
        await page.goto(`${base}/workspace?project=${remaining.project_id}&page=dashboard`);
        await page.locator('.overview-work-grid .project-management .project-state-grid').waitFor();
        await page.locator('.overview-work-grid .research-flow-card .research-flow-steps').waitFor();
        assert.equal(await page.locator('.overview-shortcut').count(),5);
        assert.equal(await page.locator('.overview-more').getAttribute('open'),null);
        assert.equal(await page.locator('.research-chain').count(),0);
        await page.screenshot({path:'docs/overview-compact-preview.png',fullPage:true});
        await page.setViewportSize({width:900,height:1000});
        await page.screenshot({path:'docs/overview-compact-narrow-preview.png',fullPage:true});
        assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
        console.log(JSON.stringify({pagination:true,search:true,selectionAcrossPages:true,cancelDelete:true,activeProjectDelete:true,batchDelete:true,noUploadFields:true,compactOverview:true,errors}));
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
