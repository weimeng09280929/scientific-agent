"""Grounded guidance + independent operation rejection; real API/temp SQLite."""
import pytest
from core.workspace.schema import GuideDraft, SuggestedItem
from core.workspace.research_coach import build_research_route
from tests.test_project_conversations import chat,create,send,messages
from tests.test_research_tasks import rt
from tests.test_conversation_home import home


def test_invalid_note_adoption_does_not_drop_independent_answer(chat):
    cid=create(chat)
    def model(*a):
        return GuideDraft(reply="条件尚未确定，可以先比较资源要求较低的路线。",
            suggestions=[SuggestedItem(kind="note",title="讨论记录",content="资源待确认")],
            actions=[{"type":"adopt_suggestion","suggestion_index":0}])
    chat.control["generate"]=model
    before=chat.services['p1'].load().revision
    first=send(chat,cid,"请先分析哪些问题更现实，不急着采纳。",'once')
    assert first.status_code==200,first.text
    m=first.json()['messages'][-1]
    assert m['content'].startswith('条件尚未确定') and m['delivery_warnings']
    assert not m['actions'] and len(first.json()['suggestions'])==1
    assert not first.json()['suggestions'][0]['applied_item_id']
    assert chat.services['p1'].load().revision==before
    repeated=send(chat,cid,"请先分析哪些问题更现实，不急着采纳。",'once')
    assert repeated.json()['messages']==first.json()['messages']
    assert len(chat.calls)==1 and len(messages(chat,cid))==2


def test_failed_action_cannot_publish_false_execution_claim(chat):
    cid=create(chat)
    chat.control['generate']=lambda *a: GuideDraft(reply="已经保存方案。",actions=[{"type":"adopt_suggestion","suggestion_id":"foreign"}])
    r=send(chat,cid)
    assert r.status_code==422 and r.json()['status']=='failed'
    assert len(messages(chat,cid))==1


def context(**changes):
    value={'topic':'联邦学习','brief':{'resources':'','time_budget':''},'history':[],'papers':[],'records':[], 'current_plan':None}
    value.update(changes)
    return value


def test_reading_and_conditions_drive_route_not_entry_label():
    c=context(conversation={'entry_stage':'design'})
    r=build_research_route(c,'我的条件不确定，先比较省资源路线')
    assert r['step']=='collect_evidence' and r['offer_search']
    assert r['conditions']['resources']['state']=='unknown'
    c['papers']=[{'paper_id':'p','title':'文献','abstract':'','excerpts':[]}]
    assert build_research_route(c,'继续')['step']=='collect_evidence'
    c['papers'][0]['abstract']='实际提供的摘要'
    r=build_research_route(c,'继续')
    assert r['step']=='read_and_gap' and not r['offer_search']
    c['records']=[{'kind':'question','item_id':'q','content':'如何比较联邦方法','title':'明确问题','needs_review':False}]
    assert build_research_route(c,'继续')['step']=='shape_method'
    c['records'][0]['needs_review']=True
    assert build_research_route(c,'继续')['step']=='review_basis'
    assert build_research_route(c,'先暂停')['step']=='paused'


@pytest.mark.parametrize('text',['先不要检索','不用联网','只阅读已有论文','谢谢，先到这里','不要按钮'])
def test_route_never_offers_unrequested_execution(text):
    assert not build_research_route(context(),text)['offer_search']


@pytest.mark.parametrize('text',[
    '请围绕当前环节讨论，不检索。',
    '开始论文阅读。请阅读已有摘要并比较方法与局限，不重复检索。',
    '现在整理研究现状和 research gap。不重新检索、不声称已证实创新。',
    '不要自动联网检索这些论文',
    "don't search for more papers; compare saved abstracts",
])
def test_discussion_steps_cannot_misinterpret_negation_as_search(text):
    from core.research_tasks.detect import classify_search_request
    assert not classify_search_request(text).is_search


def test_explicit_synthesis_focus_does_not_fall_back_to_repeated_search_questions():
    from core.workspace.guidance_policy import fallback_replies
    ctx=context(conversation={'entry_stage':'literature','discussion_step':'synthesis'})
    choices=fallback_replies('继续讨论研究现状',ctx)
    assert {c.label for c in choices}=={'比较具体研究缺口','收窄待研究的问题'}
    from core.research_tasks.detect import classify_search_request
    assert not any(classify_search_request(c.content).is_search for c in choices)


def test_proactive_card_reuses_existing_task_and_does_not_search_on_read(rt):
    rt.projects['p1'].topic='联邦学习数据差异'
    cid=rt.client.post('/api/projects/p1/conversations',json={'title':'新手讨论'}).json()['conversation']['conversation_id']
    url=f'/api/projects/p1/conversations/{cid}'
    r=rt.client.post(url+'/messages',json={'request_id':'coach','content':'条件还不确定，先帮我比较现实的路线。'})
    assert r.status_code==200,r.text
    m=r.json()['messages'][-1]
    card=next(a for a in m['actions'] if a['type']=='start_search')
    assert card['topic']=='联邦学习数据差异' and not rt.control.calls
    for _ in range(2):
        assert rt.client.get(url+'/messages').status_code==200
    assert not rt.control.calls
    # Rejected cross-conversation use cannot start a task.
    other=rt.client.post('/api/projects/p1/conversations',json={'title':'另一段讨论'}).json()['conversation']['conversation_id']
    forged=rt.client.post(f'/api/projects/p1/conversations/{other}/research-tasks',json={'request_id':'forged','content':'补文献','topic':card['topic'],'card_message_id':m['message_id'],'card_action_id':card['action_id']})
    assert forged.status_code==404 and not rt.control.calls
    started=rt.client.post(url+'/research-tasks',json={'request_id':'papers','content':'确认补充文献','topic':card['topic'],'card_message_id':m['message_id'],'card_action_id':card['action_id']})
    assert started.status_code==200,started.text
    assert started.json()['status']=='search_started'
    from tests.test_research_tasks import wait_terminal
    result=wait_terminal(rt,'p1',cid,started.json()['task_id'])
    assert result['status'] in ('completed','partial')
    assert result['saved_paper_ids']
    # Materials enter the shared project and are offered for actual reading.
    follow=rt.client.post(url+'/messages',json={'request_id':'read','content':'请比较已有摘要的局限，再讨论研究问题。'})
    assert follow.status_code==200,follow.text


def test_user_chosen_steps_persist_without_completing_research_or_calling_tools(rt,monkeypatch):
    from tests.test_project_conversations import tree
    rt.config.dry_run=False  # Exercise the offline generator's real context path.
    captured=[]
    def model(registry,area,domain,text,ctx,purpose):
        captured.append((area,ctx))
        return GuideDraft(reply='依据本次实际提供的资料讨论，不代表已经采纳。')
    monkeypatch.setattr('web.workspace_api.generate_guidance',model)
    cid=rt.client.post('/api/projects/p1/conversations',json={'title':'有步骤的研究','discussion_step':'conditions'}).json()['conversation']['conversation_id']
    url=f'/api/projects/p1/conversations/{cid}'
    # An empty workspace has a virtual updated_at until its first material write.
    # Compare actual research state and the file tree, not that display default.
    baseline=rt.services['p1'].load().model_dump(mode='json',exclude={'updated_at'})
    files=tree(rt.root)
    route=rt.client.get(url+'/research-route')
    assert route.status_code==200,route.text
    assert tree(rt.root)==files and not captured and not rt.control.calls
    assert len(route.json()['steps'])==6 and route.json()['discussion_step']=='conditions'
    assert route.json()['next_step']['id']=='exploration'
    for step,area in [('exploration','overview'),('reading','literature'),('synthesis','literature')]:
        r=rt.client.patch(url,json={'discussion_step':step})
        assert r.status_code==200,r.text
        assert r.json()['conversation']['discussion_step']==step
        assert rt.client.get(url).json()['conversation']['discussion_step']==step
        assert rt.services['p1'].load().model_dump(mode='json',exclude={'updated_at'})==baseline
        assert not rt.control.calls
        r=rt.client.post(url+'/messages',json={'request_id':step,'content':'请围绕当前环节讨论，不检索。'})
        assert r.status_code==200,r.text
        assert captured[-1][0]==area and captured[-1][1]['research_route']['discussion_step']==step
    assert not rt.control.calls
    # Selecting design neither creates a question nor bypasses the existing gate.
    rt.client.patch(url,json={'discussion_step':'design'})
    route=rt.client.get(url+'/research-route').json()
    design=next(s for s in route['steps'] if s['id']=='design')
    assert design['blocked_reason'] and not rt.services['p1'].load().items
    assert not route['planReady']
    assert rt.client.patch(url,json={'discussion_step':'experiment'}).status_code==422
    assert rt.client.get(f'/api/projects/p2/conversations/{cid}/research-route').status_code==404
    # Another chat starts independently; no inherited discussion focus.
    other=rt.client.post('/api/projects/p1/conversations',json={'title':'另一段'}).json()['conversation']['conversation_id']
    assert rt.client.get(f'/api/projects/p1/conversations/{other}/research-route').json()['discussion_step']!='design'


def test_existing_report_is_shared_index_but_not_a_substitute_for_reading(rt):
    from tests.test_research_tasks import create,start_via_message,wait_terminal
    cid=create(rt)
    run=start_via_message(rt,cid)
    t=wait_terminal(rt,'p1',cid,run['task_id'])
    assert t['status']=='completed',t
    calls=len(rt.control.calls)
    rt.client.patch(f'/api/projects/p1/conversations/{cid}',json={'discussion_step':'synthesis'})
    a=rt.client.get(f'/api/projects/p1/conversations/{cid}/research-route').json()
    assert a['discussion_step']=='synthesis' and a['saved_reports'][0]['task_id']==run['task_id']
    assert a['provided_readable_papers']>0 and len(rt.control.calls)==calls
    assert not next(s for s in a['steps'] if s['id']=='synthesis')['blocked_reason']
    assert not a['planReady'] and not rt.services['p1'].load().items


def test_first_send_step_uses_original_durable_creation_identity(home):
    from tests.test_conversation_home import start
    r=start(home,discussion_step='exploration')
    assert r.status_code==200,r.text
    identity=r.json()
    home.api._PROJECTS.clear()  # Simulate a fresh in-memory registry.
    replay=start(home,discussion_step='exploration')
    assert replay.json()==identity
    assert start(home,discussion_step='reading').status_code==409
    base=f"/api/projects/{identity['project_id']}/conversations/{identity['conversation_id']}"
    assert home.client.get(base).json()['conversation']['discussion_step']=='exploration'
    assert home.client.get(base+'/messages').json()['messages']==[]
    assert home.client.get(base+'/research-route').json()['planReady'] is False
