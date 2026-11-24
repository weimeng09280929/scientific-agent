"""P2 delivery: version-owned sources, grounded excerpts, real API/temp SQLite."""
from copy import deepcopy
import json
import re

import pytest
from core.knowledge import KnowledgeStore
from core.workspace.conversation_schema import ConversationCreate, ConversationSend
from core.workspace.schema import GuideDraft
from core.research_tasks.service import ResearchTaskService, SearchTaskRequest
from core.research_tasks.report_sources import freeze_sources, reference_index, format_prose, research_points
from core.research_tasks.synthesis import report_markdown, synthesize_report, SynthesisValidationError
from core.research_tasks.limits import SearchLimits
from tests.test_project_navigation import navigation, seed
from tests.test_conversation_home import home, tree
from tests.test_research_tasks import SourceControl, make_paper


def candidate(pid, **overrides):
    return {'paper_id':pid,'title':'Study '+pid,'authors':['Author A'],'year':2024,
            'doi':'10.0000/'+pid,'url':'https://example.org/'+pid,
            'abstract':'Provided abstract','has_abstract':True,**overrides}


def report_for(ids):
    return {'mode':'model','representative':[{'paper_id':pid,'point':'仅依摘要描述待核对的问题'} for pid in ids],
            'methods_relations':' 与 '.join('paper:'+pid for pid in ids)+' 的条件不同，不能直接比较。',
            'limitations':[{'point':'摘要未给出完整验证条件','paper_ids':ids[:1]}],
            'gaps':[],'next_steps':[],'summary':'有限资料整理'}


@pytest.mark.parametrize('scope',['metadata','abstract','abstract_excerpt','body_excerpt','full_text','unknown'])
def test_reading_scope_is_explicit_not_inferred(scope):
    r=freeze_sources(report_for(['cand-one']),[candidate('cand-one')],'model')
    r['source_snapshot']['sources'][0]['reading_scope']=scope
    text=report_markdown({'report':r})
    from core.research_tasks.report_sources import READING
    assert READING[scope] in text
    assert bool(research_points(r)) == (scope not in ('metadata','unknown'))


def test_full_ids_not_prefixes_and_repeated_citation_has_same_number():
    a,b='cand-sameprefix-111111','cand-sameprefix-222222'
    r=freeze_sources(report_for([a,b,a]),[candidate(a,title='Similar title'),candidate(b,title='Similar title')],'model')
    before=deepcopy(r)
    text=report_markdown({'report':r})
    assert [(p['paper_id'],p['number']) for p in reference_index(r)]==[(a,1),(b,2)]
    body,refs=text.split('## 参考文献与阅读范围')
    assert 'cand-' not in body and body.count('[1]')>=3 and '[2]' in body
    assert a in refs and b in refs and r==before
    assert format_prose('title: '+a+'-extended',reference_index(r)) != 'title: [1]-extended'


def test_missing_metadata_and_unknown_citation_stay_visible():
    r=freeze_sources(report_for(['cand-a']),[candidate('cand-a',authors=[],year=None,doi=None,url=None)],'model')
    r['methods_relations']='paper:cand-missing 尚未核实'
    text=report_markdown({'report':r})
    assert '作者：缺失' in text and '年份：缺失' in text and 'DOI：缺失' in text
    assert '原文链接：缺失' in text and '无法可靠还原' in text
    assert 'cand-missing' not in text.split('## 参考文献与阅读范围')[0]
    assert reference_index(r)[1]['missing_snapshot'] is True


def test_new_snapshot_cannot_upgrade_library_full_text_and_metadata_skips_synthesis():
    c=candidate('cand-a',reading_scope='full_text')
    r=freeze_sources(report_for(['cand-a']),[c],'model')
    assert r['source_snapshot']['sources'][0]['reading_scope']=='abstract_excerpt'
    calls=[]
    report,note=synthesize_report('topic',[],[candidate('cand-meta',abstract='',has_abstract=False)],[],SearchLimits(),lambda *a:calls.append(a))
    assert calls==[] and report['mode']=='rules'
    frozen=freeze_sources(report,[candidate('cand-meta',abstract='',has_abstract=False)],'rules')
    assert not research_points(frozen)
    assert '不能据此分析' in report_markdown({'report':frozen})


def setup(n, monkeypatch, *, partial=False, metadata=False, excluded=False, invalid=False, truncated=False):
    from core.research_tasks import sources
    from web.research_task_api import build_research_task_router
    chat=seed(n,'delivery-test')
    cid=chat.create(ConversationCreate(title='引用交付验收'))['conversation_id']
    control=SourceControl()
    control.default=lambda name,q,seq:[make_paper(seq,q,doi=f'10.0000/{seq}',abstract='' if metadata else 'Provided abstract')]
    funcs={'openalex':control('openalex'),'arxiv':control('arxiv')}
    if partial:
        def unavailable(*a,**k):raise TimeoutError('offline timeout')
        funcs['arxiv']=unavailable
    monkeypatch.setattr(sources,'SOURCE_FUNCS',funcs)
    model_calls=[]
    def model(step,payload):
        model_calls.append(step)
        if step=='plan':return json.dumps({'queries':[{'query':'federated learning','rationale':'offline'}]})
        if step=='rank':return json.dumps({'rankings':[{'index':i,'relevant':not(excluded and i==1),'reason':'offline decision'} for i in range(5)]})
        if truncated:return '{"representative":'
        data=json.loads(payload['prompt'].split('候选文献：',1)[1].split('\n来源执行情况',1)[0])
        return json.dumps(report_for(['cand-forged'] if invalid else [p['paper_id'] for p in data[:1]]),ensure_ascii=False)
    svc=ResearchTaskService(chat.project_id,chat.db_path,chat,model_fn=model,runner=lambda fn:fn())
    n.client.app.include_router(build_research_task_router(n.config,n.api._require_project,lambda project:svc),prefix='/api/projects')
    return chat,cid,svc,control,model_calls


def start(svc,cid,rid='first'):
    queued=svc.start(cid,SearchTaskRequest(request_id=rid,content='请检索联邦学习文献并整理现状',topic='联邦学习'))
    return svc.get(queued['task_id'])


def test_delivery_actual_citations_separate_from_saved_directory(navigation,monkeypatch):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,excluded=True)
    task=start(svc,cid);assert task['status']=='completed',task.get('error')
    msgs=chat.messages(cid)['messages'];reply=next(m for m in msgs if m.get('research_task_id'))
    assert '研究要点' in reply['content'] and '查询：' not in reply['content']
    assert len(reply['cited_paper_ids'])==1 and task['saved_papers_count']==2
    assert len(reply['suggested_replies'])==2 and len(reply['actions'])==2
    assert len(reply['suggested_replies'])+len(reply['actions'])<=4
    assert all(a['type']=='view_references' for a in reply['actions'])
    actual,catalog=reply['actions']
    assert actual['paper_ids']==reply['cited_paper_ids'] and len(catalog['paper_ids'])==2
    assert chat.perform_action(cid,reply['message_id'],actual['action_id'])['paper_ids']==reply['cited_paper_ids']
    assert not chat.shared.load(include_chat=False).items
    state=tree(n.root);before=(len(calls),len(control.calls),len(msgs))
    document=svc.report_document(task['task_id'])
    url=f'/api/projects/{chat.project_id}/research-tasks/{task["task_id"]}/export'
    assert n.client.get(url).text==task['report_markdown']==document
    assert n.client.get(url+'?version=1').text==document
    directory=n.client.get(url+'?version=1&catalog=true').text
    assert '非正文引用' in directory and '筛选排除' in directory
    start(svc,cid)
    assert before==(len(calls),len(control.calls),len(chat.messages(cid)['messages']))
    assert state==tree(n.root)


def test_resume_and_metadata_updates_do_not_change_old_version(navigation,monkeypatch):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,partial=True)
    task=start(svc,cid);assert task['status']=='partial'
    original=svc.report_document(task['task_id'],1)
    metadata=chat.shared.store.list_papers()[0];metadata.title='LATER TITLE';metadata.authors=['Later Author'];KnowledgeStore(chat.db_path).save_paper(metadata)
    assert svc.report_document(task['task_id'],1)==original
    from core.research_tasks import sources
    monkeypatch.setitem(sources.SOURCE_FUNCS,'arxiv',control('arxiv'))
    svc.retry(task['task_id'],resume=True)
    current=svc.get(task['task_id']);assert current['current_version']==2,current.get('error')
    assert svc.report_document(task['task_id'],1)==original
    assert 'LATER TITLE' not in original and 'Author A' in original
    assert svc.report_document(task['task_id'],2)==current['report_markdown']
    assert '[1]' in original and '[1]' in current['report_markdown']


@pytest.mark.parametrize('kind',['legacy','metadata','invalid','zero','failed'])
def test_honest_degraded_states_and_no_read_writes(navigation,monkeypatch,kind):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,metadata=kind=='metadata',invalid=kind=='invalid')
    if kind=='zero':control.default=lambda *a:[]
    if kind=='failed':control.script=[TimeoutError('offline')]*2
    task=start(svc,cid)
    if kind=='legacy':
        with svc._connection(True) as conn:
            row=svc._row(conn,task['task_id']);r=json.loads(row['report']);r.pop('source_snapshot');versions=json.loads(row['report_versions']);versions[0]['report']=r;versions[0].pop('render_context')
            conn.execute('UPDATE sra_research_tasks SET report=?,report_versions=? WHERE task_id=?',(json.dumps(r),json.dumps(versions),task['task_id']))
        original=svc.report_document(task['task_id'],1)
        assert '未保存可确认的来源快照' in original and 'Author A' not in original
        paper=chat.shared.store.list_papers()[0];paper.title='MUST NOT BACKFILL';KnowledgeStore(chat.db_path).save_paper(paper)
        assert svc.report_document(task['task_id'],1)==original
    else:
        replies=[m for m in chat.messages(cid)['messages'] if m.get('research_task_id')]
        if kind in ('invalid','failed'):
            assert task['status']=='failed' and not replies
            assert '尚未产出报告' in svc.report_document(task['task_id'])
        else:
            assert replies and '**研究要点' not in replies[-1]['content']
            assert not replies[-1]['cited_paper_ids']
            if kind=='metadata':assert 'synthesize' not in calls
    state=tree(n.root);usage=(len(calls),len(control.calls))
    svc.get(task['task_id']);svc.report_document(task['task_id']);svc.report_document(task['task_id'],catalog=True)
    assert tree(n.root)==state and usage==(len(calls),len(control.calls))


def test_fake_relation_ids_are_rejected_not_normalized_into_success():
    data=report_for(['cand-valid']);data['methods_relations']='paper:cand-not-provided 有不同方法'
    with pytest.raises(SynthesisValidationError):
        synthesize_report('topic',[],[candidate('cand-valid')],[],SearchLimits(),lambda *a:json.dumps(data))


def test_truncated_synthesis_delivers_only_honestly_labelled_catalog(navigation,monkeypatch):
    chat,cid,svc,control,calls=setup(navigation,monkeypatch,truncated=True)
    task=start(svc,cid)
    assert task['mode']=='rules' and task['content_status']=='synthesis_failed_catalog_only'
    assert '模型内容整理未成功' in task['status_label']
    reply=next(m for m in chat.messages(cid)['messages'] if m.get('research_task_id'))
    assert not reply['cited_paper_ids'] and '模型内容整理未成功' in reply['content']
    assert '**研究要点' not in reply['content']
    assert '不是正常研究内容综合' in svc.report_document(task['task_id'],1)


def test_next_discussion_keeps_version_local_numbers_without_cross_chat_history(navigation,monkeypatch):
    chat,cid,svc,control,calls=setup(navigation,monkeypatch)
    task=start(svc,cid)
    seen=[]
    def guidance(area,text,context,purpose):
        seen.append(context['history'])
        return GuideDraft(reply='可以核对历史编号，不能用编号代替当前论文依据。'),'rules'
    chat.send(cid,ConversationSend(content='解释刚才引用[1]的含义',request_id='followup'),'离线',guidance)
    maps=[m['report_reference_map'] for m in seen[-1] if m.get('report_reference_map')]
    assert len(maps)==1 and maps[0]['version']==1 and maps[0]['task_id']==task['task_id']
    assert maps[0]['citations'][0]['number']==1 and maps[0]['citations'][0]['paper_id'] in task['saved_paper_ids']
    other=chat.create(ConversationCreate(title='另一段独立讨论'))['conversation_id']
    chat.send(other,ConversationSend(content='解释研究问题',request_id='other'),'离线',guidance)
    assert not any(m.get('report_reference_map') for m in seen[-1])
