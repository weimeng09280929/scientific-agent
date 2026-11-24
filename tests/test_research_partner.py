"""Software contracts, not evidence of live-model tutoring quality."""
import pytest
from core.workspace.schema import GuideDraft,SuggestedItem
from core.workspace.guidance_policy import ROLE
from tests.research_partner_fixture import sample as fixture,create,send,workspace,brief
from tests.local_backup_fixture import expect


@pytest.fixture
def sample(tmp_path):
    with fixture(tmp_path) as value:yield value


@pytest.mark.parametrize('scenario,mode,text,length',[
    ('broad','beginner','我只对人工智能感兴趣，不知道从哪里开始。','adaptive'),
    ('direction','direct','我已确定做学习资料检索，想比较不同切入点。','adaptive'),
    ('known','beginner','已有资源条件下怎样讨论可行性？','adaptive'),
    ('conflict','direct','我现在有一块 GPU，可否改用另一种路线讨论？','adaptive'),
    ('term','beginner','请只解释基线和消融实验，用例子详细说明。','detailed'),
    ('title','direct','只知道这篇论文的标题，怎么核对它的贡献？','adaptive'),
    ('abstract','direct','摘要提到一种重排方法，能否据此判断全文贡献？','adaptive'),
    ('idea','direct','把两个检索模块结合是不是肯定有创新？','adaptive'),
    ('brief','beginner','简短一点，只解释基线，一句话。','brief'),
    ('explain','direct','只解释，不要生成方案或继续提问。','adaptive'),
    ('pause','beginner','先暂停一下吧','adaptive'),
])
def test_actual_website_provider_receives_policy_scope_and_preferences(sample,scenario,mode,text,length):
    s=sample
    if scenario=='broad':
        s['pid']=expect(s['a'].post('/api/projects',json={'topic':'人工智能','domain':'ai'}))['project_id'];brief(s)
    if scenario in ('known','conflict'):brief(s,goal='比较学习资料检索的方法',resources='只有 CPU，没有 GPU',time_budget='四周')
    cid=create(s,guidance_mode=mode,entry_stage='question' if scenario=='idea' else 'orientation')
    before=workspace(s);raw='【离线输出样例，不代表模型质量】回答正文原样传递。'
    s['control']['result']=GuideDraft(reply=raw,questions=['本轮必要核对点？'] if scenario=='known' else [])
    result=expect(send(s,cid,text));message=result['messages'][-1];assert message['content']==raw
    assert len(s['calls'])==1 and result['attempt']==1
    call=s['calls'][0];ctx=call['context'];prefs=ctx['response_preferences']
    assert call['system'].count(ROLE)==1 and ctx['conversation']['guidance_mode']==mode
    assert ctx['history']==[] and ctx['context_scope']['history_messages']==0
    assert prefs['length']==length
    assert call['max_tokens_override']==(6144 if length=='detailed' or scenario=='idea' else 4096)
    if scenario in ('known','conflict'):assert ctx['brief']['resources']=='只有 CPU，没有 GPU'
    if scenario=='conflict':assert call['text']==text and workspace(s)['brief']==before['brief']
    expected={} if scenario=='broad' else {'title-only':'metadata','abstract-only':'abstract'}
    assert {p['paper_id']:p['content_available'] for p in ctx['papers']}==expected
    assert ctx['paper_scope']['total_in_project']==len(expected) and ctx['paper_scope']['abstracts_provided']==0
    assert ctx['paper_scope']['text_previews_provided']==(1 if expected else 0)
    if expected:
        assert '未提供摘要或正文' in next(p['scope'] for p in ctx['papers'] if p['paper_id']=='title-only')
        assert '未提供全文' in next(p['scope'] for p in ctx['papers'] if p['paper_id']=='abstract-only')
    assert workspace(s)==before
    saved=expect(s['a'].get(f"/api/projects/{s['pid']}/conversations/{cid}"))['conversation']
    assert saved['guidance_mode']==mode
    if prefs['paused'] or prefs['explain_only']:
        assert not any(message[k] for k in ('questions','suggested_replies','actions','suggestion_ids'))
        assert not ctx['research_route']['offer_search']
    assert s['state']['public_calls']==s['state']['source_calls']==0


@pytest.mark.parametrize('kind',['foreign_citation','invalid_selector','false_execution','invalid_schema'])
def test_output_guards_run_before_publishing_even_for_only_explain(sample,kind):
    s=sample;cid=create(s);before=workspace(s)
    bad={'foreign_citation':GuideDraft(reply='离线故障注入',cited_paper_ids=['foreign-paper']),
         'invalid_selector':GuideDraft(reply='离线故障注入',actions=[{'type':'adopt_suggestion','suggestion_id':'foreign-draft'}]),
         'false_execution':GuideDraft(reply='已经保存方案。',suggestions=[SuggestedItem(title='不应提交的笔记',content='仅为离线故障注入')]),
         'invalid_schema':{'reply':'离线故障注入','innovation_pattern':'fake-extra-field'}}[kind]
    s['control']['result']=bad;result=send(s,cid,'只解释这个问题，不进行任何操作。')
    # Invalid actions may be independently rejected, but no action is published.
    if kind=='invalid_selector':
        assert result.status_code==200 and not result.json()['messages'][-1]['actions']
        assert result.json()['messages'][-1]['delivery_warnings']
    else:
        assert result.status_code==(502 if kind=='invalid_schema' else 422) and result.json()['status']=='failed'
        assert result.json()['failure']['reason']=='structure_invalid' and not result.json()['failure']['can_retry']
        assert len(result.json()['messages'])==1
    assert workspace(s)==before and len(s['calls'])==1


def test_method_draft_still_requires_question_and_explicit_adoption(sample):
    s=sample;cid=create(s)
    denied=expect(send(s,cid,'请生成计算研究方案',rid='before-question',plan_request={'method':'computational'}))
    assert denied['messages'][-1]['mode']=='rules' and not denied['suggestions'] and not s['calls']
    w=workspace(s)
    question=expect(s['a'].post(f"/api/projects/{s['pid']}/workspace/items",json={'expected_revision':w['revision'],'item':{'kind':'question','title':'明确确认的检索比较问题','content':'如何在相同资料条件下讨论两种检索方法？','paper_ids':['abstract-only']}}))['workspace']['items'][-1]
    result=expect(send(s,cid,'请生成计算研究方案',rid='draft',plan_request={'method':'computational','question_id':question['item_id']}))
    proposal=result['suggestions'][0];assert proposal['plan']['question_id']==question['item_id']
    assert proposal['applied_item_id'] is None and [r['kind'] for r in workspace(s)['items']]==['question']
    assert s['calls'][-1]['max_tokens_override']==12000 and s['calls'][-1]['context']['plan_generation']['allowed']
    applied=expect(s['a'].post(f"/api/projects/{s['pid']}/conversations/{cid}/suggestions/{proposal['suggestion_id']}/apply",json={}))
    assert applied['item']['plan_version']==1 and len(s['calls'])==1


def test_moved_history_is_separate_and_new_chat_has_no_other_chat_memory(sample):
    s=sample;brief(s,resources='只有 CPU，没有 GPU');brief(s,pid=s['target'],resources='已有一块 GPU，仅用于离线软件验收')
    cid=create(s);expect(send(s,cid,'我只有CPU，没有GPU，先讨论资源限制。',rid='old-text'))
    prefix=f"/api/projects/{s['pid']}/conversations/{cid}";pre=expect(s['a'].get(prefix+'/move-precheck',params={'target_project_id':s['target']}));assert pre['can_move']
    expect(s['a'].post(prefix+'/move',json={'target_project_id':s['target'],'request_id':'move','expected_token':pre['token'],'acknowledge_history':True}))
    old=s['pid'];s['pid']=s['target'];before=workspace(s)
    expect(send(s,cid,'只解释当前资源如何用于讨论，不保存新条件。',rid='target-turn'))
    ctx=s['calls'][-1]['context'];assert ctx['brief']['resources']==before['brief']['resources'] and not ctx['history']
    prior=ctx['conversation']['prior_project_history'];assert len(prior)==2 and all(r['origin_project_id']==old for r in prior)
    fresh=create(s);expect(send(s,fresh,'简短说明本次能参考哪些资料。',rid='new-chat'))
    ctx=s['calls'][-1]['context'];assert ctx['history']==[] and ctx['context_scope']['history_messages']==0
    assert not ctx['conversation'].get('prior_project_history') and workspace(s)==before


def test_long_explanation_is_preserved_and_structured_followup_stays_bounded(sample):
    s=sample;cid=create(s)
    raw='【离线长度与原文保真测试，不是模型质量示例】\n'+('解释样例，不能当作实测结果。'*70)
    s['control']['result']=GuideDraft(reply=raw,questions=['核对点一？','核对点二？','核对点三？'])
    result=expect(send(s,cid,'详细展开当前问题，不检索。'));m=result['messages'][-1]
    assert len(raw)>700 and m['content']==raw and len(m['questions'])==2
    assert s['calls'][-1]['max_tokens_override']==6144 and len(s['calls'])==1


def test_pause_does_not_publish_false_execution_claim(sample):
    s=sample;cid=create(s);before=workspace(s)
    s['control']['result']=GuideDraft(reply='已经保存方案。',suggestions=[SuggestedItem(title='故障注入',content='不能用暂停清除操作再声称成功')])
    result=send(s,cid,'先暂停一下吧');assert result.status_code==422
    assert result.json()['status']=='failed' and workspace(s)==before


def test_selected_excerpts_are_bounded_and_not_labeled_full_text(sample):
    from core.knowledge.schema import PaperChunk
    s=sample
    s['store'].save_paper_chunks([PaperChunk(chunk_id='chunk-'+str(i),paper_id='title-only',chunk_index=i,text='离线局部段落 '+str(i),page=i+1) for i in range(4)])
    cid=create(s);expect(send(s,cid,'只解释本次提供的“局部段落”的阅读范围，不检索。',selected_paper_ids=['title-only']))
    ctx=s['calls'][-1]['context'];assert len(ctx['papers'])==1
    p=ctx['papers'][0];assert p['content_available']=='excerpts' and len(p['excerpts'])==3
    assert [e['page'] for e in p['excerpts']]==[1,2,3] and '未提供全文' in p['scope'] and not p['abstract']


@pytest.mark.parametrize('text',['我已确定做学习资料检索，想比较不同切入点。','把两个检索模块结合是不是有创新？','信息检索方向如何缩小范围？','搜索算法的基线是什么意思？'])
def test_retrieval_as_topic_is_not_execution_authorization(text):
    from core.research_tasks.detect import classify_search_request
    assert not classify_search_request(text).is_search


@pytest.mark.parametrize('text',['请检索学习资料检索方向的代表性论文。','开始搜索 RAG 的代表性论文。','检索 RAG 的相关论文'])
def test_explicit_search_entry_still_authorizes_existing_task(text):
    from core.research_tasks.detect import classify_search_request
    result=classify_search_request(text);assert result.is_search and result.explicit


@pytest.mark.parametrize('scenario',['term','known','conflict','pause'])
def test_explicit_rule_mode_is_honest_and_does_not_call_provider(sample,scenario):
    s=sample;s['api']._CONFIG.dry_run=True;brief(s,goal='已有检索方向',resources='只有 CPU，没有 GPU',time_budget='四周')
    cid=create(s,guidance_mode='beginner',entry_stage='design');before=workspace(s)
    text={'term':'只解释基线和消融实验，并举例。','known':'看看方法可行性','conflict':'我现在有一块GPU，能讨论另一条路线吗？','pause':'先暂停一下吧'}[scenario]
    result=expect(send(s,cid,text));m=result['messages'][-1]
    assert m['mode']=='rules' and '未调用模型' in m['content'] and not s['calls']
    assert workspace(s)==before
    if scenario in ('known','term','pause'):assert m['questions']==[]
    if scenario=='conflict':assert '只有 CPU，没有 GPU' in m['content'] and '我现在有一块GPU' in m['content'] and len(m['questions'])==1
    if scenario=='term':assert '用来比较的已有方法' in m['content'] and '去掉一个部件' in m['content'] and not m['actions']
