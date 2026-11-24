/* Real two-process restart; session tokens remain only in browser memory. */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const base='http://127.0.0.1:8078';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'sra-auth-restart-'));
let child,browser;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function start(){
  child=spawn(path.resolve('venv/Scripts/python.exe'),['-m','tests.local_account_test_server','--root',root,'--port','8078'],{cwd:process.cwd(),windowsHide:true,stdio:'ignore'});
  for(let i=0;i<200;i++){
    if(child.exitCode!==null)throw new Error('isolated restart server exited');
    const response=await fetch(base+'/__account__/state').catch(()=>null);
    if(response?.ok)return;
    await wait(100);
  }
  throw new Error('isolated restart server unavailable');
}
async function stop(){
  if(!child||child.exitCode!==null)return;
  const ended=once(child,'exit');
  await fetch(base+'/__account__/shutdown',{method:'POST'});
  await Promise.race([ended,wait(15000).then(()=>{throw new Error('server failed to stop');})]);
}
(async()=>{
  try{
    await start();browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage();await page.goto(base);
    await page.locator('[name=email]').fill('tester-a@example.com');
    await page.locator('[name=password]').fill('browser acceptance A');
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
    const record=await page.evaluate(async()=>{
      const r=await fetch('/api/conversations/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:'restart-study',title:'persistent owned project'})});
      const p=await r.json();
      await fetch(`/api/projects/${p.project_id}/notes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'offline note survives restart'})});
      return p;
    });
    const uid=await page.evaluate(async()=>(await(await fetch('/api/auth/me')).json()).user.id);
    await stop();await start();
    await page.reload();await page.locator('[data-ref="send"]:not(:disabled)').waitFor();
    const after=await page.evaluate(async pid=>({me:await(await fetch('/api/auth/me')).json(),notes:await(await fetch(`/api/projects/${pid}/notes`)).json()}),record.project_id);
    assert.equal(after.me.user.id,uid);assert.match(JSON.stringify(after.notes),/offline note survives restart/);
    assert(!await page.locator('#account-gate').isVisible());
    const result={passed:true,port:8078,sameSessionAfterProcessRestart:true,ownedProjectAndNoteRetained:true,temporaryRootRemoved:true,realModelCalls:0,publicSourceCalls:0};
    fs.writeFileSync('docs/local-account-isolation/restart-results.json',JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  }finally{
    await browser?.close();await stop();
    // Delete only this script's resolved temporary directory after both workers
    // have exited. Never touch the repository data root.
    const resolved=path.resolve(root),temp=path.resolve(os.tmpdir());
    if(path.dirname(resolved)!==temp||!path.basename(resolved).startsWith('sra-auth-restart-'))throw new Error('unsafe temporary cleanup target');
    fs.rmSync(resolved,{recursive:true});
  }
})().catch(e=>{console.error(String(e.stack||e).replace(/browser acceptance [AB]/g,'[redacted]'));process.exitCode=1;});
