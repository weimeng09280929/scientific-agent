/* Read-only browser fixture: no projects, experiments, model requests or uploads. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1280,height:1000}});
    const errors=[],external=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
        const u=route.request().url();
        if(u.startsWith('http://127.0.0.1:8001/')) return route.continue();
        external.push(u);return route.abort();
    });
    await page.route('**/document-math-test',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><div id="document"></div>'}));
    try {
        await page.goto('http://127.0.0.1:8001/document-math-test');
        await page.addStyleTag({url:'http://127.0.0.1:8001/static/research-document.css'});
        for(const path of ['vendor/marked/marked.min.js','vendor/dompurify/purify.min.js','research-document.js','vendor/mathjax/tex-svg-full.js'])
            await page.addScriptTag({url:'http://127.0.0.1:8001/static/'+path});
        const sample=String.raw`### 符号与公式
| 符号 | 含义 |
|---|---|
| \(C=|\mathcal{C}|\) | 类别数量 |
| \(\|x\|_2\) | 向量范数 |
| $\theta_i^t$ | 参数 |

\[
W_i = \big(A_i+(\lambda+\mu)I\big)^{-1}(B_i+\mu W_g)
\]

$$\begin{cases}x,&x>0\\0,&\text{其他}\end{cases}$$

\(\frac{1}{\) 暂不完整，必须保留原文。

代码：

`;
        const code='\n```text\n\\(not_math\\)\n```\n`$not_math$`';
        const hostile='<img src="https://invalid.test/pixel" onerror="window.PWNED=1"><script>window.PWNED=1</script><a href="javascript:alert(1)">危险链接</a>';
        await page.evaluate(async text=>{await SRA_DOCUMENT.render(document.getElementById('document'),text);},sample+code+hostile);
        assert.equal(await page.locator('tbody tr').count(),3);
        assert.equal(await page.locator('tbody tr:first-child td').count(),2);
        assert.equal(await page.locator('mjx-container').count(),5);
        assert.equal(await page.locator('mjx-assistive-mml').count(),0);
        assert.equal(await page.locator('.document-math-error').count(),1);
        assert.equal(await page.locator('#document img,#document script,#document a[href^="javascript:"]').count(),0);
        assert.equal(await page.evaluate(()=>window.PWNED),undefined);
        assert.match(await page.locator('pre code').innerText(),/not_math/);
        await page.evaluate(()=>SRA_DOCUMENT.toggleSource(document.getElementById('document')));
        assert.equal(await page.locator('mjx-container').count(),0);
        await page.evaluate(async()=>{SRA_DOCUMENT.toggleSource(document.getElementById('document'));await new Promise(r=>setTimeout(r,100));});
        assert.equal(await page.locator('mjx-container').count(),5);
        await page.evaluate(async()=>{
            const n=document.getElementById('document');
            const old=n.innerHTML;
            await Promise.all([SRA_DOCUMENT.render(n,'旧文档 \\(a\\)'),SRA_DOCUMENT.render(n,'新文档 \\(b\\)')]);
            if(!n.textContent.includes('新文档') || n.textContent.includes('旧文档')) throw Error('stale render');
            await SRA_DOCUMENT.render(n,'\\[x^2+\\frac{1}{y}\\]');
        });
        // A separate read-only run can load the user's pasted sample, without copying it into the repo.
        if(process.argv[2]) {
            const pasted=fs.readFileSync(process.argv[2],'utf8');
            await page.evaluate(async text=>SRA_DOCUMENT.render(document.getElementById('document'),text),pasted);
            assert.ok(await page.locator('mjx-container').count()>50);
            assert.equal(await page.locator('.document-math-error').count(),0);
            assert.equal(await page.locator('table').first().locator('tbody tr').first().locator('td').count(),2);
        }
        const downloadPromise=page.waitForEvent('download');
        await page.evaluate(()=>SRA_DOCUMENT.download(document.getElementById('document'),'公式测试'));
        const download=await downloadPromise;
        const file=await download.path(),html=fs.readFileSync(file,'utf8');
        assert.match(html,/<svg/);assert.doesNotMatch(html,/<script|<use/);
        await page.screenshot({path:'.pytest_cache/math-document-desktop.png'});
        await page.setViewportSize({width:390,height:844});
        await page.screenshot({path:'.pytest_cache/math-document-preview.png'});
        assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
        console.log(JSON.stringify({ok:true,equations:await page.locator('mjx-container').count(),externalRequests:external.length}));
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
