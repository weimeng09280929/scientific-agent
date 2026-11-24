"""Overview inputs remain read-only and protected by the real account API."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from tests.local_backup_fixture import app_at, login
from tests.project_overview_test_server import seed, snapshot


@pytest.fixture(scope='module')
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp('overview')
    from core.local_accounts import AccountStore
    AccountStore(root/'accounts.db').initialize()
    with app_at(root) as (api, state):
        data = seed(api)
        with TestClient(api.app) as a, TestClient(api.app) as b, TestClient(api.app) as anonymous:
            login(a, 0);login(b, 1)
            yield api, state, data['users'], a, b, anonymous


INPUTS = ['/workspace?include_chat=false', '/workspace/materials?kind=plans&limit=3', '/notes', '/conversations?limit=1']


def test_read_inputs_keep_name_versions_drafts_and_project_bytes(sample):
    api, state, users, a, _, _ = sample
    user = users[0];base = '/api/projects/'+user['project_id']
    before, calls = snapshot(api), dict(state)
    view = a.get(base+INPUTS[0]).json()
    assert view['project']['display_name'] == '人工智能RAG'
    assert view['workspace']['brief']['time_budget'] == '四周'
    assert view['workflow']['flow']['planReady'] is True
    assert len(view['papers']) == 1 and not view['workspace']['messages']
    plans = a.get(base+INPUTS[1]).json()
    assert [x['key'] for x in plans['entries']] == ['plan:'+user['plan_id']]
    assert [x['suggestion_id'] for x in plans['drafts']] == [user['draft_id']]
    assert plans['entries'][0]['version'] == 'v1 · '+user['plan_id'][:8]
    assert next(i for i in view['workspace']['items'] if i['item_id']==user['plan_id'])['plan_version']==1
    assert len(a.get(base+INPUTS[2]).json()['notes']) == 1
    assert len(a.get(base+INPUTS[3]).json()['conversations']) == 1
    assert a.get(base+'/conversations/'+user['conversation_id']).status_code == 200
    assert snapshot(api) == before and state == calls


@pytest.mark.parametrize('suffix', INPUTS+['/conversations/{cid}'])
def test_overview_sources_reject_other_account_and_anonymous(sample, suffix):
    _, _, users, a, b, anonymous = sample
    u=users[0];url='/api/projects/'+u['project_id']+suffix.format(cid=u['conversation_id'])
    assert a.get(url).status_code == 200
    assert b.get(url).status_code == 404
    assert anonymous.get(url).status_code == 401


def test_empty_project_does_not_gain_conversation_or_formal_material(sample):
    api, state, users, a, _, _ = sample
    base='/api/projects/'+users[0]['empty_project_id']
    before, calls = snapshot(api), dict(state)
    data=a.get(base+INPUTS[0]).json()
    assert data['workspace']['items'] == [] and data['workspace']['brief']['goal'] == ''
    assert data['workflow']['flow']['question'] is None and not data['workflow']['flow']['planReady']
    assert a.get(base+INPUTS[3]).json()['conversations'] == []
    assert snapshot(api)==before and state==calls


def test_nested_conversation_cannot_be_combined_with_another_owned_project(sample):
    api, _, users, a, _, _ = sample
    before=snapshot(api);u=users[0]
    assert a.get('/api/projects/'+u['empty_project_id']+'/conversations/'+u['conversation_id']).status_code==404
    assert snapshot(api)==before
