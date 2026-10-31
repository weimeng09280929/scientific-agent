"""Real routes backing the folders: metadata, identity and explicit first send."""
from fastapi.testclient import TestClient
import pytest
from tests.local_backup_fixture import app_at, expect, login
from tests.project_organization_test_server import prepare
from tests.project_overview_test_server import snapshot


@pytest.fixture(scope='module')
def sample(tmp_path_factory):
    root=tmp_path_factory.mktemp('organization')
    from core.local_accounts import AccountStore
    AccountStore(root/'accounts.db').initialize()
    with app_at(root) as (api,state):
        data=prepare(api)
        with TestClient(api.app) as a,TestClient(api.app) as b,TestClient(api.app) as anonymous:
            login(a,0);login(b,1)
            yield api,state,data['organization'],a,b,anonymous


def test_same_name_projects_and_chats_keep_distinct_ownership_and_messages(sample):
    api,state,data,a,_,_=sample
    before,calls=snapshot(api),dict(state)
    projects=expect(a.get('/api/projects?limit=10'))['projects']
    names=[p for p in projects if p['display_name']=='人工智能RAG']
    assert {p['project_id'] for p in names}=={data['first_project'],data['second_project']}
    selected=[]
    for key in ('first_project','second_project'):
        pid=data[key];rows=expect(a.get(f'/api/projects/{pid}/conversations?limit=100'))['conversations']
        assert all(c['project_id']==pid for c in rows)
        same=[c for c in rows if c['title']=='同名对话'];assert len(same)>=2
        cid=same[0]['conversation_id'];selected.append(cid)
        messages=expect(a.get(f'/api/projects/{pid}/conversations/{cid}/messages'))['messages']
        assert len(messages)==1 and pid[-6:] in messages[0]['content']
    assert selected[0]!=selected[1]
    assert snapshot(api)==before and state==calls


def test_folder_pagination_reads_only_metadata_without_duplicates(sample):
    api,state,data,a,_,_=sample
    before,calls=snapshot(api),dict(state);pid=data['first_project'];seen=[];offset=0
    while offset is not None:
        result=expect(a.get(f'/api/projects/{pid}/conversations?limit=5&offset={offset}'))
        assert len(result['conversations'])<=5
        for row in result['conversations']:
            assert 'messages' not in row and 'content' not in row
            assert row['project_id']==pid;seen.append(row['conversation_id'])
        offset=result['next_offset']
    assert len(seen)==len(set(seen))==8
    assert set(seen)=={c['conversation_id'] for c in data['first_project_conversations']}
    assert snapshot(api)==before and state==calls


def test_recent_shortcut_points_to_the_same_saved_conversation(sample):
    api,_,data,a,_,_=sample
    before=snapshot(api)
    for row in expect(a.get('/api/conversations/recent?limit=50'))['conversations']:
        detail=expect(a.get(f"/api/projects/{row['project_id']}/conversations/{row['conversation_id']}"))['conversation']
        assert detail['project_id']==row['project_id'] and detail['title']==row['title']
        assert row['project_name'] and 'messages' not in row
    assert snapshot(api)==before


def test_folder_and_known_identifiers_do_not_cross_accounts(sample):
    api,_,data,a,b,anonymous=sample
    before=snapshot(api);pid=data['first_project'];cid=data['first_project_conversations'][0]['conversation_id']
    assert pid not in {p['project_id'] for p in expect(b.get('/api/projects'))['projects']}
    assert pid not in {c['project_id'] for c in expect(b.get('/api/conversations/recent'))['conversations']}
    for suffix in ('/conversations?limit=5',f'/conversations/{cid}',f'/conversations/{cid}/messages'):
        assert a.get('/api/projects/'+pid+suffix).status_code==200
        assert b.get('/api/projects/'+pid+suffix).status_code==404
        assert anonymous.get('/api/projects/'+pid+suffix).status_code==401
    assert a.get('/api/projects/'+data['second_project']+'/conversations/'+cid).status_code==404
    assert expect(a.get('/api/projects/'+data['blank_project']+'/conversations'))['conversations']==[]
    assert snapshot(api)==before


def test_explicit_first_send_reuses_request_and_keeps_new_chat_independent(sample):
    api,state,data,a,_,_=sample
    before,calls=snapshot(api),dict(state);pid=data['second_project']
    body={'project_id':pid,'request_id':'organized-new-chat','title':'一次新的具体讨论','domain':'ai'}
    first=expect(a.post('/api/conversations/start',json=body));again=expect(a.post('/api/conversations/start',json=body))
    assert first['project_id']==pid and again==first
    cid=first['conversation_id'];url=f'/api/projects/{pid}/conversations/{cid}/messages'
    assert expect(a.get(url))['messages']==[]
    message={'request_id':'organized-first-message','content':'先解释怎样核对证据，不生成方案。','wait_for_completion':True}
    assert expect(a.post(url,json=message))['status']=='completed'
    assert expect(a.post(url,json=message))['status']=='completed'
    assert [r['role'] for r in expect(a.get(url))['messages']]==['user','assistant']
    assert state['guidance_calls']==calls['guidance_calls']+1
    after=snapshot(api)
    assert after[pid]['counts']['sra_conversations']==before[pid]['counts']['sra_conversations']+1
    for other in before:
        if other!=pid:assert before[other]==after[other]
