/* Read-only integration: existing project GETs, intercepted preview text, no writes. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[],writes=[];
    const pid='proj_20260916_090459_bb40d6';
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',route=>{
        if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}
        return route.continue();
    });
    await page.route(`**/api/projects/${pid}/download/method-doc`,route=>route.fulfill({contentType:'text/markdown',body:String.raw`## 方法预览测试
| 符号 | 含义 |
|---|---|
| \(C=|\mathcal C|\) | 类别数 |

\[W_i=(A_i+\lambda I)^{-1}B_i\]

只读界面夹具，不是研究结果。`}));
    try {
        await page.goto(`http://127.0.0.1:8001/workspace?project=${pid}&page=experiments`);
        await page.getByRole('button',{name:'查看方法文档',exact:true}).click();
        const card=page.locator('.card').filter({has:page.getByRole('button',{name:'查看方法文档',exact:true})});
        await card.locator('mjx-container').first().waitFor();
        assert.equal(await card.locator('mjx-container').count(),2);
        assert.equal(await card.locator('tbody tr td').count(),2);
        await card.getByRole('button',{name:'查看原文',exact:true}).click();
        assert.match(await card.locator('.document-source').innerText(),/\\lambda/);
        await card.getByRole('button',{name:'返回公式预览',exact:true}).click();
        await card.locator('mjx-container').first().waitFor();
        const downloaded=page.waitForEvent('download');
        await card.getByRole('button',{name:'下载公式版 HTML',exact:true}).click();
        assert.equal((await downloaded).suggestedFilename(),'research-document.html');
        const opened=page.waitForEvent('popup');
        await card.getByRole('button',{name:'公式版打印 / PDF',exact:true}).click();
        const popup=await opened;
        await popup.locator('svg').first().waitFor();
        assert.equal(await popup.locator('svg').count(),2);
        assert.equal(await popup.locator('script,mjx-assistive-mml').count(),0);
        await popup.close();
        await page.getByRole('button',{name:'查看方法文档',exact:true}).click();
        assert.equal(await card.locator('.research-document').isVisible(),false);
        assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
        console.log(JSON.stringify({methodPreview:true,sourceToggle:true,htmlDownload:true,printPreview:true,projectWrites:0}));
    } finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
