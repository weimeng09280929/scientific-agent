"""API/provider-edge contracts, not proof of live teaching quality."""
import pytest
from pydantic import ValidationError
from core.workspace.schema import GuideDraft,ReplyOption
from tests.research_partner_fixture import sample as fixture,create,send,workspace
from tests.local_backup_fixture import expect

@pytest.fixture
def sample(tmp_path):
    with fixture(tmp_path) as s:yield s

@pytest.mark.parametrize('text',['我学材料，什么是混杂因素？请用例子解释。','RAG 是什么？不是我的领域。','没听懂','还是没听懂，再简单一点','只解释对照，简短一点','先暂停一下吧'])
def test_real_chain_respects_confusion_and_scope(sample,text):
    s=sample;cid=create(s,guidance_mode='direct');before=workspace(s)
    s['control']['result']=GuideDraft(reply='【人工预期示例】其他条件可能同时改变比较结果，这类因素叫混杂因素；这里只说明含义。',suggested_replies=[ReplyOption(label='比较条件',content='请比较条件',explanation='整理待确认条件')])
    result=expect(send(s,cid,text));ctx=s['calls'][-1]['context'];m=result['messages'][-1]
    assert s['calls'][-1]['system'].count('【稳定身份】')==1
    assert ctx['conversation']['guidance_mode']=='direct' and workspace(s)==before
    if any(t in text for t in ('没听懂','不是我的领域')):assert ctx['response_preferences']['needs_reexplanation'] and not m['suggested_replies'] and not m['questions']
    if '只解释' in text or '暂停' in text:assert not m['suggested_replies'] and not m['actions']

def test_three_explanations_persist_and_only_selected_content_is_sent(sample):
    s=sample;cid=create(s);options=[ReplyOption(label=f'选择{i}',content=f'请讨论比较条件{i}。',explanation=f'比较第{i}种条件，得到待确认清单。') for i in range(3)]
    s['control']['result']=GuideDraft(reply='离线选项渲染样本',suggested_replies=options)
    r=expect(send(s,cid,'比较三种讨论方式。'));m=r['messages'][-1]
    assert m['suggested_replies']==[o.model_dump() for o in options]
    refreshed=expect(s['a'].get(f"/api/projects/{s['pid']}/conversations/{cid}/messages?tail=true"))['messages'][-1]
    assert refreshed['suggested_replies']==m['suggested_replies']
    before=workspace(s);expect(send(s,cid,options[1].content,'chosen'))
    assert s['calls'][-1]['text']==options[1].content and workspace(s)==before and len(s['calls'])==2
    route=expect(s['a'].get(f"/api/projects/{s['pid']}/conversations/{cid}/research-route"))
    assert all(step['expectation'] and step['purpose'] for step in route['steps'])

def test_old_options_and_length_bound():
    assert ReplyOption(label='旧标题',content='旧内容').explanation is None
    with pytest.raises(ValidationError):ReplyOption(label='标题',content='内容',explanation='长'*161)

def test_legacy_saved_message_without_new_field_reads_unchanged(sample):
    import sqlite3,json
    from contextlib import closing
    s=sample;cid=create(s);s['control']['result']=GuideDraft(reply='旧消息兼容样本',suggested_replies=[ReplyOption(label='旧选项',content='请讨论条件')])
    expect(send(s,cid,'解释选项兼容'));db=s['api']._CONFIG.paths.project_db(s['pid'])
    with closing(sqlite3.connect(db)) as c:
        for seq,raw in c.execute('SELECT seq,data FROM sra_chat_messages WHERE conversation_id=?',(cid,)).fetchall():
            m=json.loads(raw)
            for option in m.get('suggested_replies',[]):option.pop('explanation',None)
            c.execute('UPDATE sra_chat_messages SET data=? WHERE conversation_id=? AND seq=?',(json.dumps(m),cid,seq))
        c.commit()
    before=db.read_bytes();r=expect(s['a'].get(f"/api/projects/{s['pid']}/conversations/{cid}/messages?tail=true"))
    assert r['messages'][-1]['suggested_replies'][0]['label']=='旧选项' and db.read_bytes()==before

def test_explanation_cannot_promise_execution(sample):
    s=sample;cid=create(s);s['control']['result']=GuideDraft(reply='离线故障注入',suggested_replies=[ReplyOption(label='比较',content='请比较方法',explanation='点击自动保存并保证创新成立')])
    r=send(s,cid,'讨论方法');assert r.status_code==422 and r.json()['failure']['reason']=='structure_invalid'
    assert not workspace(s)['items'] and len(s['calls'])==1

def test_explanation_can_truthfully_disclaim_automatic_save(sample):
    s=sample;cid=create(s);s['control']['result']=GuideDraft(reply='离线正常说明样本',suggested_replies=[ReplyOption(label='比较',content='请比较方法',explanation='讨论方法的区别，不会自动保存。')])
    r=expect(send(s,cid,'讨论方法'));assert r['messages'][-1]['suggested_replies'][0]['explanation']=='讨论方法的区别，不会自动保存。'
