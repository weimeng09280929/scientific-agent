/* 仅访问 8012 临时服务；不启动模型、科研流水线或实验。 */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const base='http://127.0.0.1:8012', errors=[], started=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(/\/(run|run-topic-discovery|regenerate-method-doc|guide|backfill)(\?|$)/.test(r.url()))started.push(r.url());});
    try {
        const created=await page.request.post(base+'/api/projects',{data:{topic:'领域收敛界面测试',domain:'security'}});
        assert.equal(created.status(),200);
        const pid=(await created.json()).project_id;
        const denied=await page.request.post(base+'/api/projects',{data:{topic:'不应创建的测试项目',domain:'medicine'}});
        assert.equal(denied.status(),422);
        await page.goto(`${base}/workspace?project=${pid}&page=create`);
        const select=page.locator('#domain-select');
        await select.waitFor();
        assert.deepEqual(await select.locator('option').evaluateAll(nodes=>nodes.map(n=>n.value)),['computer_science','ai','security']);
        assert.equal(await page.locator('#domain-custom-input').count(),0);
        await page.locator('.session-options > summary').click();
        assert.match(await page.locator('#research-scope-notice').innerText(),/暂不支持医学/);
        await select.selectOption('security');
        assert.match(await page.locator('#domain-hint').innerText(),/明确授权/);
        await select.selectOption('computer_science');
        assert.match(await page.locator('#domain-hint').innerText(),/系统可靠性/);
        // Simulate a legacy project's capability metadata only in this browser.
        // API rejection and preservation are separately tested against real SQLite.
        await page.route(`**/api/projects/${pid}/workspace`,async route=>{
            const response=await route.fetch(), data=await response.json();
            data.research_scope={supported:false,notice:'历史领域暂不启动自动指导；材料保留。'};
            await route.fulfill({response,json:data});
        });
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('rg-mode')?.textContent==='历史领域 · 可查看记录');
        assert.equal(await page.locator('#rg-send').isDisabled(),true);
        assert.match(await page.locator('#rg-send').getAttribute('title'),/已有对话、笔记/);
        assert.deepEqual(started,[]);
        assert.deepEqual(errors,[]);
        console.log('PASS: three computing domains, boundary notice, per-domain hints, API rejection, legacy guidance disabled; no model or pipeline calls');
    } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
