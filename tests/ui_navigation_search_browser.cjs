/* Read-only navigation search against the actual FastAPI catalog. */
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base=process.env.SRA_NAV_TEST_URL||'http://127.0.0.1:8057';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
 try{
  await page.goto(base+'/');
  const search=page.locator('#ch-conversation-search');await search.waitFor();await page.waitForFunction(()=>!document.querySelector('#ch-conversation-search').disabled);
  await search.fill('长历史分页验收');
  const row=page.locator('#ch-conversations .ch-conversation-row');await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length===1&&document.querySelector('#ch-conversations .ch-conversation-row').textContent.includes('长历史分页验收'));
  await row.locator('.ch-conversation-link').click();
  await page.waitForFunction(()=>new URL(location.href).searchParams.has('conversation')&&!document.querySelector('[data-ref="send"]').disabled);
  assert.equal(new URL(page.url()).searchParams.get('conversation'),await row.getAttribute('data-conversation-id'));
  await row.locator('.ch-row-menu > summary').focus();await page.keyboard.press('Enter');await row.getByRole('button',{name:'重命名',exact:true}).waitFor({state:'visible'});
  await page.keyboard.press('Escape');assert.equal(await row.locator('.ch-row-menu').evaluate(n=>n.open),false);
  await search.fill('不会存在的科研对话');await page.waitForFunction(()=>!document.querySelector('#ch-conversations .ch-conversation-row'));
  await search.fill('同名研究讨论');await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length===5);
  const projectIds=await row.evaluateAll(nodes=>nodes.map(n=>n.dataset.projectId));assert(new Set(projectIds).size>1);
  const before=await row.count();await page.locator('.ch-more-conversations').click();await page.waitForFunction(n=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length>n,before);
  await page.locator('.ch-history-scroll').evaluate(n=>n.scrollTop=n.scrollHeight);
  const common=await page.locator('.ch-current-materials').boundingBox();assert(common.y>=0&&common.y+common.height<900);
  await page.locator('#ch-conversation-collapse').click();await page.waitForFunction(()=>document.querySelectorAll('#ch-conversations .ch-conversation-row').length===5);assert(await row.count()<=5);
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);console.log(JSON.stringify({ok:true,search:true,projectOwnership:true,keyboardMenu:true,pinnedMaterials:true,noWrites:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
