/* Read-only checks against the explicitly requested live local entrances. */
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve('docs/local-registration-dual-entry');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const demo=await browser.newContext({viewport:{width:1440,height:900}}),accounts=await browser.newContext({viewport:{width:390,height:844}});
  const d=await demo.newPage(),a=await accounts.newPage(),errors=[];
  for(const page of [d,a])page.on('pageerror',e=>errors.push(e.message));
  try{
    await d.goto('http://127.0.0.1:8001/');await d.locator('[data-ref="send"]:not(:disabled)').waitFor();
    assert.equal(await d.locator('#account-gate').isVisible(),false);
    assert.match(await d.locator('#account-email').textContent(),/无需登录/);
    assert.equal(await d.locator('#account-logout').isVisible(),false);
    const list=await(await d.request.get('http://127.0.0.1:8001/api/projects?limit=100')).json();
    assert(list.total>0);
    await d.screenshot({path:path.join(out,'live-demo-8001-desktop.png')});
    await a.goto('http://127.0.0.1:8077/');await a.getByRole('button',{name:'注册新账号',exact:true}).waitFor();
    assert.equal(await a.locator('.app').isVisible(),false);
    assert.equal((await a.request.get('http://127.0.0.1:8077/api/projects')).status(),401);
    await a.getByRole('button',{name:'注册新账号',exact:true}).click();
    await a.screenshot({path:path.join(out,'live-account-8077-register-narrow.png')});
    assert.equal(await a.locator('[name=password]').getAttribute('minlength'),'8');
    assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    const result={passed:true,demoPort:8001,accountPort:8077,demoProjects:list.total,demoNeedsLogin:false,accountAnonymousProjects:401,registrationVisible:true,passwordMinLength:8,realProjectMutations:0,pageErrors:errors};
    fs.writeFileSync(path.join(out,'live-entry-results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
