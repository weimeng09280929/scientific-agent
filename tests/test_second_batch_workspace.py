"""Batch 2: actual routers, temporary SQLite; no public sources or models."""
from contextlib import closing
import json
import sqlite3

import pytest
from core.knowledge import KnowledgeStore

from tests.test_project_navigation import navigation, seed
from tests.test_conversation_home import home, tree
from tests.test_research_tasks import SourceControl
from core.workspace.conversation_schema import ConversationCreate, ConversationSend
from core.workspace.schema import GuideDraft, SuggestedItem, ItemInput, Brief
from core.research_tasks.service import ResearchTaskService, SearchTaskRequest


def setup_project(n, pid='batch-two'):
    chat=seed(n,pid)
    cid=chat.create(ConversationCreate(title='当前讨论'))['conversation_id']
    return chat,cid


def search(chat,cid,monkeypatch,partial=False):
    from core.research_tasks import sources
    control=SourceControl()
    def unavailable(*a,**k):
        raise TimeoutError('offline timeout fixture')
    monkeypatch.setattr(sources,'SOURCE_FUNCS',{'openalex':control('openalex'),'arxiv':unavailable if partial else control('arxiv')})
    svc=ResearchTaskService(chat.project_id,chat.db_path,chat,runner=lambda fn:fn())
    return svc,svc.start(cid,SearchTaskRequest(request_id='search-one',content='查找联邦学习资料',topic='联邦学习'))


def test_shared_progress_not_executor_created_and_reviews_remain_authoritative(navigation):
    n=navigation;chat,cid=setup_project(n)
    s=chat.shared
    s.add_item(ItemInput(kind='question',title='比较问题',content='未验证的研究问题'),0)
    q=s.load(include_chat=False).items[-1]
    s.add_item(ItemInput(kind='plan',title='正式方案',content='仅方案，不是结果',related_ids=[q.item_id]),1)
    before=tree(n.root)
    page=n.client.get('/api/projects?limit=5').json()['projects'][0]
    material=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=plans').json()
    activities=n.client.get(f'/api/projects/{chat.project_id}/activities').json()
    assert page['status']=='created'
    assert page['research_progress']['planReady'] is True
    assert material['progress']['label']==activities['progress']['label']==page['research_progress']['label']=='方案可审阅'
    assert len(material['entries'])==1 and material['entries'][0]['label']=='当前正式方案'
    assert tree(n.root)==before
    s.save_brief(Brief(resources='用户新条件'),2)
    result=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=plans').json()
    assert result['progress']['needs_review'] and not result['progress']['planReady']
    assert result['workflow']['flow']['plan']['needs_review']
    assert n.api._PROJECTS[chat.project_id].status=='created'


def test_report_material_export_and_activity_share_task_version(navigation,monkeypatch):
    n=navigation;chat,cid=setup_project(n)
    svc,task=search(chat,cid,monkeypatch,partial=True)
    task=svc.get(task['task_id']);assert task['status']=='partial'
    before=tree(n.root)
    entries=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries']
    exported=n.client.get(f'/api/projects/{chat.project_id}/exports').json()['entries']
    e=next(e for e in entries if e.get('task_id')==task['task_id'])
    same=next(x for x in exported if x['key']==e['key'])
    assert e['url']==same['url'] and e['status']=='partial' and '部分完成' in e['status_label']
    material=n.client.get(f'/api/projects/{chat.project_id}/workspace?include_chat=false').json()
    assert material['material_counts']['research_reports']==1
    assert n.client.get(f'/api/projects/{chat.project_id}/status').json()['material_counts']['research_reports']==1
    downloaded=n.client.get(e['url'])
    assert downloaded.status_code==200 and task['topic'] in downloaded.text
    rows=n.client.get(f'/api/projects/{chat.project_id}/activities').json()['activities']
    assert len(rows)==1 and rows[0]['id']==task['task_id'] and rows[0]['status']=='partial'
    assert rows[0]['conversation_id']==cid
    assert tree(n.root)==before


def test_report_versions_are_exact_read_only_directory_entries(navigation,monkeypatch):
    n=navigation;chat,cid=setup_project(n)
    svc,task=search(chat,cid,monkeypatch)
    task=svc.get(task['task_id'])
    with svc._connection(True) as conn:
        original=json.loads(conn.execute('SELECT report_versions FROM sra_research_tasks WHERE task_id=?',(task['task_id'],)).fetchone()[0])
        assert len(original)==1
        second={**original[0],'version':2,'status':'partial','delivered_at':'2026-10-23T00:00:00'}
        conn.execute('UPDATE sra_research_tasks SET report_versions=? WHERE task_id=?',(json.dumps(original+[second]),task['task_id']))
    before=tree(n.root)
    m=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries']
    assert [e['version'] for e in m]==[2,1]
    assert m[0]['status']=='partial' and m[1]['status']=='completed'
    assert m[0]['url'].endswith('?version=2') and m[1]['url'].endswith('?version=1')
    assert m[0]['key']!=m[1]['key']
    assert n.client.get(m[1]['url']).status_code==200
    assert tree(n.root)==before


def test_activities_paginate_requests_not_phases_or_attempts(navigation):
    n=navigation;chat,cid=setup_project(n)
    for i in range(3):
        chat.send(cid,ConversationSend(content='离线普通讨论',request_id=f'r{i}'),'离线项目',lambda *a: (GuideDraft(reply='仅离线软件回复'),'model'))
    before=tree(n.root)
    one=n.client.get(f'/api/projects/{chat.project_id}/activities?limit=2').json()
    two=n.client.get(f'/api/projects/{chat.project_id}/activities?limit=2&offset=2').json()
    assert len(one['activities'])==2 and one['next_offset']==2
    assert len(two['activities'])==1 and two['next_offset'] is None
    assert len({x['id'] for x in one['activities']+two['activities']})==3
    assert all(x['kind']=='guidance' and x['status']=='completed' for x in one['activities']+two['activities'])
    assert tree(n.root)==before


def test_plan_drafts_are_project_materials_not_other_chat_history(navigation):
    n=navigation;chat,cid=setup_project(n)
    other=chat.create(ConversationCreate(title='另一段讨论'))['conversation_id']
    question=chat.shared.add_item(ItemInput(kind='question',title='问题',content='待回答的问题'),0).items[0]
    def generation(*a):
        from core.workspace.plans import rule_plan
        return rule_plan(a[2]),'model'
    response=chat.send(cid,ConversationSend(content='请为当前问题生成研究方案',request_id='draft-one',plan_request={'method':'computational'}),'离线项目',generation)
    assert response['status']=='completed', response.get('error')
    m=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=plans').json()
    assert len(m['drafts'])==1 and m['drafts'][0]['conversation_id']==cid and not m['drafts'][0]['basis_changed']
    assert chat.messages(other)['messages']==[]
    assert chat.shared.load(include_chat=False).items==[question]
    chat.shared.save_brief(Brief(resources='已变更'),1)
    m=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=plans').json()
    assert m['drafts'][0]['basis_changed'] and not m['progress']['planReady']


@pytest.mark.parametrize('suffix',['workspace/materials?kind=plans','workspace/materials?kind=reports','activities'])
def test_missing_database_and_legacy_reads_never_initialize(navigation,suffix):
    n=navigation;chat,cid=setup_project(n)
    missing='missing-db'
    n.api._PROJECTS[missing]=n.api.ProjectState(project_id=missing,topic='缺失库',domain='ai',created_at='2026-10-23')
    before=tree(n.root)
    assert n.client.get(f'/api/projects/{missing}/{suffix}').status_code==404
    assert n.client.get(f'/api/projects/absent/{suffix}').status_code==404
    assert n.client.get(f'/api/projects/{chat.project_id}/{suffix}').status_code==200
    assert tree(n.root)==before


def test_teaching_fork_handoff_is_persisted_without_confirming_mainline(navigation):
    n=navigation
    start=n.client.post('/api/teaching-cases/start',json={'request_id':'case-start','case_id':'fl-heterogeneity','version':'1.1.0'}).json()
    base=f"/api/projects/{start['project_id']}/conversations/{start['conversation_id']}/teaching"
    request={'request_id':'fork-same','title':'自己的联邦学习问题','resources':'我的CPU','time_budget':'四周'}
    a=n.client.post(base+'/fork',json=request)
    assert a.status_code==200,a.text
    b=n.client.post(base+'/fork',json=request)
    assert a.json()['project_id']==b.json()['project_id']
    pid=a.json()['project_id'];before=tree(n.root)
    workspace=n.client.get(f'/api/projects/{pid}/workspace?include_chat=false').json()
    assert workspace['teaching'] is None
    assert len(workspace['teaching_reference']['source_titles'])==3
    assert workspace['workspace']['brief']['resources']=='我的CPU'
    assert not workspace['workflow']['flow']['question'] and not workspace['workflow']['flow']['planReady']
    assert tree(n.root)==before


def test_legacy_material_and_task_reads_do_not_migrate_chat(navigation):
    from core.workspace.schema import Workspace, Message
    from core.workspace.service import KEY
    n=navigation;chat=seed(n,'legacy-batch-two')
    w=Workspace(project_id=chat.project_id,messages=[Message(role='assistant',mode='model',content='原始历史对话',area='overview')])
    store=KnowledgeStore(chat.db_path)
    store.save_kv(KEY,w.model_dump(mode='json'))
    store.save_kv('cross_validation_report',{'gaps':['待查证的历史问题']})
    before=tree(n.root)
    for suffix in ['workspace/materials?kind=plans','workspace/materials?kind=reports','activities']:
        r=n.client.get(f'/api/projects/{chat.project_id}/'+suffix)
        assert r.status_code==200,r.text
    assert n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries'][0]['key']=='research-report'
    assert tree(n.root)==before
    with closing(sqlite3.connect(chat.db_path)) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_conversations'").fetchone()


def test_request_read_projects_are_separate_and_source_content_invalidates_draft(navigation):
    from core.knowledge.schema import Paper,PaperChunk
    from core.workspace.plans import rule_plan
    n=navigation;chat,cid=setup_project(n,'A')
    second,other=setup_project(n,'B')
    store=KnowledgeStore(chat.db_path)
    store.save_paper(Paper(paper_id='owned',title='项目A资料',abstract='仅为软件夹具'))
    question=chat.shared.add_item(ItemInput(kind='question',title='问题',content='待回答',paper_ids=['owned']),0).items[0]
    result=chat.send(cid,ConversationSend(content='生成研究方案',request_id='one',plan_request={'method':'computational'}),'仅夹具',lambda area,text,ctx,purpose:(rule_plan(ctx),'model'))
    assert result['status']=='completed',result.get('error')
    assert n.client.get('/api/projects/B/activities').json()['activities']==[]
    assert n.client.get('/api/projects/B/workspace/materials?kind=plans').json()['drafts']==[]
    assert n.client.get(f'/api/projects/B/conversations/{cid}').status_code==404
    store.save_paper_chunks([PaperChunk(chunk_id='changed',paper_id='owned',chunk_index=0,text='正文内容发生变化')])
    draft=n.client.get('/api/projects/A/workspace/materials?kind=plans').json()['drafts'][0]
    assert draft['basis_changed'] is True
    assert n.client.post(f"/api/projects/A/conversations/{cid}/suggestions/{draft['suggestion_id']}/apply",json={}).status_code==409


def test_legacy_plan_drafts_paginate_without_migration_or_new_basis(navigation):
    from core.workspace.schema import Workspace,Message,Suggestion
    from core.workspace.service import KEY
    n=navigation;chat=seed(n,'legacy-many-drafts')
    w=Workspace(project_id=chat.project_id,
        messages=[Message(role='assistant',content='历史草案',area='study',mode='model')],
        suggestions=[Suggestion(suggestion_id=f'old-{i}',kind='plan',title=f'历史方案{i}',content='保留历史正文',based_on_revision=0) for i in range(45)])
    KnowledgeStore(chat.db_path).save_kv(KEY,w.model_dump(mode='json'))
    before=tree(n.root);seen=[]
    for offset,expected,next_offset in [(0,20,20),(20,20,40),(40,5,None)]:
        r=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=plans&limit=20&offset={offset}')
        assert r.status_code==200,r.text
        data=r.json();assert len(data['drafts'])==expected and data['draft_next_offset']==next_offset
        assert all(s['basis_changed'] and not s['material_fingerprint'] for s in data['drafts'])
        seen.extend(s['suggestion_id'] for s in data['drafts'])
    assert len(set(seen))==45 and tree(n.root)==before
