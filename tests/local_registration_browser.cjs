const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base='http://127.0.0.1:8087';
const output=path.resolve('docs/local-registration-dual-entry');
const secrets=[];
async function register(page,email){
  const password=crypto.randomBytes(2).toString('hex')+'aZ9!';secrets.push(password);
  await page.goto(base);await page.getByRole('button',{name:'注册新账号',exact:true}).click();
  await page.locator('[name=email]').fill(email);
  await page.locator('[name=password]').fill(password);
  await page.locator('[name=confirm_password]').fill(password);
  await page.getByRole('button',{name:'创建账号',exact:true}).click();
  await page.getByText('注册成功，邮箱尚未验证。请使用你刚设置的密码登录。',{exact:true}).waitFor();
  assert.equal(await page.locator('[name=password]').inputValue(),'');
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
  return password;
}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const ca=await browser.newContext({viewport:{width:1440,height:900}}),cb=await browser.newContext({viewport:{width:390,height:844}});
  const a=await ca.newPage(),b=await cb.newPage(),errors=[],outside=[];
  for(const ctx of [ca,cb])await ctx.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(outside.push(r.request().url()),r.abort()));
  a.on('pageerror',e=>errors.push(e.message));b.on('pageerror',e=>errors.push(e.message));
  try{
    await a.goto(base);await a.getByRole('button',{name:'注册新账号',exact:true}).waitFor();
    await a.screenshot({path:path.join(output,'login-with-register-desktop.png')});
    await a.getByRole('button',{name:'注册新账号',exact:true}).click();
    assert.equal(await a.locator('[name=password]').getAttribute('minlength'),'8');
    await a.screenshot({path:path.join(output,'register-desktop.png')});
    await b.goto(base);await b.getByRole('button',{name:'注册新账号',exact:true}).click();
    await b.screenshot({path:path.join(output,'register-narrow.png')});
    assert.equal(await b.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const pa=await register(a,'browser-register-a@example.com');
    await register(b,'browser-register-b@example.com');
    const project=await a.evaluate(async()=>{
      const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:'registered user A only'})});
      return r.json();
    });
    assert(project.project_id);
    assert.equal(await b.evaluate(async pid=>(await fetch(`/api/projects/${pid}/status`)).status,project.project_id),404);
    assert.equal(await b.evaluate(async()=>(await(await fetch('/api/projects')).json()).projects.length),0);
    assert.match(await a.locator('#account-email').textContent(),/邮箱未验证/);
    await a.locator('#account-logout').click();await a.locator('#account-gate').waitFor({state:'visible'});
    await a.getByRole('button',{name:'注册新账号',exact:true}).click();
    await a.locator('[name=email]').fill('BROWSER-REGISTER-A@example.com');
    await a.locator('[name=password]').fill(pa);await a.locator('[name=confirm_password]').fill(pa);
    await a.getByRole('button',{name:'创建账号',exact:true}).click();
    await a.getByText('该邮箱暂时无法注册，请核对或使用已有账号登录',{exact:true}).waitFor();
    await a.screenshot({path:path.join(output,'duplicate-email-desktop.png')});
    const audit=await(await a.request.get(base+'/__account__/state')).json();
    assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
    assert.equal(audit.model_calls,0);assert.equal(audit.source_calls,0);assert.equal(audit.public_calls,0);
    const result={passed:true,passwordLength:8,newAccountsCanLogin:true,emailVerified:false,twoUserIsolation:true,duplicateNormalizedEmailRejected:true,desktopAnd390px:true,pageErrors:0,externalBrowserRequests:0,realModelCalls:0,publicSourceCalls:0};
    fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{let message=String(e.stack||e);for(const secret of secrets)message=message.split(secret).join('[redacted]');console.error(message);process.exitCode=1;});
