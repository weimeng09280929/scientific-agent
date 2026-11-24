"""Library audit joins real saved tasks; reads never backfill the legacy log."""
from core.workspace.conversation_schema import ConversationCreate
from tests.test_project_navigation import navigation, seed
from tests.test_conversation_home import home, tree
from tests.test_second_batch_workspace import setup_project, search


def evidence_client(n):
    n.client.app.add_api_route('/api/projects/{project_id}/evidence', n.api.list_evidence, methods=['GET'])
    return n.client


def test_new_task_evidence_visible_without_rewriting_old_log(navigation, monkeypatch):
    n = navigation
    chat, cid = setup_project(n)
    svc, task = search(chat, cid, monkeypatch, partial=True)
    before = tree(n.root)
    response = evidence_client(n).get(f'/api/projects/{chat.project_id}/evidence')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['entries'] == [] and body['stats']['total'] == 0
    audit = body['conversation_search']['tasks'][0]
    assert audit['task_id'] == task['task_id'] and audit['conversation_id'] == cid
    assert audit['status'] == 'partial' and audit['failed_sources'] == ['arxiv']
    saved = svc.get(task['task_id'])
    assert audit['source_calls'] == saved['source_calls']
    assert audit['coverage'] == saved['report']['coverage']
    assert audit['has_report'] is True
    assert tree(n.root) == before


def test_audit_pagination_project_ownership_and_empty_legacy(navigation, monkeypatch):
    n = navigation
    chat, cid = setup_project(n)
    ids = []
    for i in range(3):
        c = cid if i == 0 else chat.create(ConversationCreate(title=f'讨论{i}'))['conversation_id']
        _, task = search(chat, c, monkeypatch)
        ids.append(task['task_id'])
    other, other_cid = setup_project(n, 'other')
    search(other, other_cid, monkeypatch)
    seed(n, 'legacy-no-chat')
    before = tree(n.root)
    client = evidence_client(n)
    url = f'/api/projects/{chat.project_id}/evidence?limit=2'
    a = client.get(url).json()['conversation_search']
    b = client.get(url+'&offset=2').json()['conversation_search']
    assert a['next_offset'] == 2 and b['next_offset'] is None
    assert set(x['task_id'] for x in a['tasks']+b['tasks']) == set(ids)
    assert client.get('/api/projects/legacy-no-chat/evidence').json()['conversation_search'] == {'tasks': [], 'next_offset': None}
    assert client.get(url+'&offset=-1').status_code == 422
    assert client.get(url.replace('limit=2', 'limit=31')).status_code == 422
    assert tree(n.root) == before


def test_missing_database_is_not_created(navigation):
    n = navigation
    evidence_client(n)
    n.api._PROJECTS['missing-db'] = n.api.ProjectState(project_id='missing-db', topic='只读', created_at='2026-10-23')
    before = tree(n.root)
    response = n.client.get('/api/projects/missing-db/evidence')
    assert response.status_code == 404 and '数据库' in response.json()['detail']
    assert tree(n.root) == before
