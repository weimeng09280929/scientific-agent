/* Real main app and API, temporary SQLite, SDK MockTransport. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.SRA_P1_URL || 'http://127.0.0.1:8066';
const shots=path.resolve(process.env.SRA_P1_SHOTS || 'docs/p1-recovery-20261024');

(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  page.on('dialog',d=>d.accept());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const api=page.request;
  try{
    const fixture=await(await api.get(base+'/__fixture__/state')).json();
    const pid=fixture.project_id;
    const created=await(await api.post(`${base}/api/projects/${pid}/conversations`,{data:{title:'P1 恢复验收',guidance_mode:'direct'}})).json();
    const cid=created.conversation.conversation_id;
    const url=`${base}/workspace?page=dashboard&project=${pid}&conversation=${cid}`;
    await page.goto(url);
    await page.locator('#ch-input').fill('请解释这个研究问题的范围');
    await page.locator('[data-ref="send"]').click();
    await page.getByRole('button',{name:'重试此消息'}).waitFor({timeout:45000});
    assert.equal((await(await api.get(base+'/__p1__/state')).json()).attempts,4);
    await page.getByRole('button',{name:'重试此消息'}).click();
    await page.getByRole('button',{name:'重新发起本次生成'}).waitFor({timeout:45000});
    assert.equal((await(await api.get(base+'/__p1__/state')).json()).attempts,6);
    assert.equal(await page.getByRole('button',{name:'重试此消息'}).count(),0);
    await page.screenshot({path:path.join(shots,'budget-exhausted-desktop.png')});
    await page.reload();
    await page.getByRole('button',{name:'重新发起本次生成'}).waitFor({timeout:15000});
    await api.post(base+'/__p1__/recover');
    await page.getByRole('button',{name:'重新发起本次生成'}).click();
    await page.getByText('恢复后的离线回复；尚未采纳任何正式材料。').waitFor({timeout:30000});
    const attempts=(await(await api.get(base+'/__p1__/state')).json()).attempts;
    assert.equal(attempts,7);
    const rows=(await(await api.get(`${base}/api/projects/${pid}/conversations/${cid}/messages?tail=true&limit=100`)).json()).messages;
    assert.equal(rows.filter(m=>m.role==='user').length,1);
    assert.equal(rows.filter(m=>m.role==='assistant').length,1);
    await page.screenshot({path:path.join(shots,'recovered-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.reload();
    await page.getByText('恢复后的离线回复；尚未采纳任何正式材料。').waitFor({timeout:15000});
    await page.getByText('新一轮处理：completed。原请求仍保留为失败。').waitFor({timeout:15000});
    assert.equal(await page.getByRole('button',{name:'重试此消息'}).count(),0);
    await page.getByText('正在核对会话能力...').waitFor({state:'hidden',timeout:15000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(shots,'recovered-narrow.png')});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({pid,cid,attempts,userMessages:1,assistantMessages:1,errors,shots},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
