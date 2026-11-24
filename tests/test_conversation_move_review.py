"""Explicit negative outcomes and token fences, real FastAPI/SQLite, offline."""
from contextlib import closing
import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest
from tests.conversation_move_fixture import fixture
from tests.test_conversation_moves import base,check,move,material
from tests.local_backup_fixture import expect,app_at,login
from tests.project_overview_test_server import snapshot
from core.workspace.conversation_moves import ConversationMover,REFUSALS
from core.workspace.conversations import ConversationNotFound


@pytest.fixture
def sample(tmp_path):
    with fixture(tmp_path) as s:yield s


def changed(s):
    old=material(s,s['target'])
    expect(s['a'].put(f"/api/projects/{s['target']}/workspace/brief",json={'expected_revision':old['revision'],'brief':{'resources':'另一标签页核对后的资源条件'}}))


def test_refusal_is_bound_persistent_and_old_token_never_executes(sample):
    s=sample;old=check(s);changed(s);before=snapshot(s['api'])
    response=move(s,old['token'],rid='review-refusal');assert response.status_code==409
    result=response.json()['detail']
    assert result['move_state']=='not_executed' and result['request_id']=='review-refusal'
    assert result['target_project_id']==s['target'] and result['expected_token']==old['token']
    assert expect(s['a'].get(base(s)+'/move-requests/review-refusal'))==result
    assert {p:r['counts'] for p,r in snapshot(s['api']).items()}=={p:r['counts'] for p,r in before.items()}
    assert move(s,old['token'],rid='review-refusal').json()['detail']==result
    new=check(s);assert new['token']!=old['token']
    assert expect(move(s,new['token'],rid='review-refusal'))['move_state']=='completed'
    assert expect(s['a'].get(base(s)+'/move-requests/review-refusal'))['move_state']=='completed'


def test_unknown_absence_is_read_only_and_completed_409_is_not_refusal(sample):
    s=sample;before=snapshot(s['api'])
    absent=s['a'].get(base(s)+'/move-requests/not-yet-received');assert absent.status_code==404
    assert snapshot(s['api'])==before
    old=check(s);expect(move(s,old['token']))
    completed=move(s,old['token'],target=s['third']);assert completed.status_code==409
    assert completed.json()['detail']['move_state']=='completed'
    assert completed.json()['detail']['project_id']==s['target']
    relocated=move(s,old['token'],rid='different-late-request')
    assert relocated.status_code==409 and relocated.json()['detail']['code']=='conversation_moved'
    assert relocated.json()['detail'].get('move_state')!='not_executed'


def test_cannot_promise_non_execution_when_refusal_storage_fails(sample):
    s=sample;old=check(s);changed(s)
    with patch.object(ConversationMover,'record_refusal',side_effect=sqlite3.OperationalError('offline injected write failure')):
        result=move(s,old['token'],rid='storage-unknown')
    assert result.status_code==503 and isinstance(result.json()['detail'],str)
    assert s['a'].get(base(s)+'/move-requests/storage-unknown').status_code==404
    assert expect(s['a'].get(base(s)))['conversation']['conversation_id']==s['cid']


def test_unreadable_completion_cannot_be_misreported_as_non_execution(sample):
    s=sample;pre=check(s)
    with patch.object(ConversationMover,'status',side_effect=ConversationNotFound('offline outcome read failure')):
        result=move(s,pre['token'],rid='completed-but-unreadable')
    assert result.status_code==503 and isinstance(result.json()['detail'],str)
    assert expect(s['a'].get(base(s,s['target'])))['conversation']['conversation_id']==s['cid']
    assert expect(s['a'].get(base(s)+'/move-requests/completed-but-unreadable'))['move_state']=='completed'
    with closing(sqlite3.connect(s['api']._CONFIG.paths.project_db(s['source']))) as conn:
        assert conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(REFUSALS,)).fetchone() is None


def test_refusal_fence_survives_backup_restore_and_new_target_is_explicit(tmp_path):
    from core.local_backup import backup,restore
    root=tmp_path/'source';out=tmp_path/'backup';restored=tmp_path/'restored'
    with fixture(root) as s:
        old=check(s);changed(s);assert move(s,old['token'],rid='fenced').status_code==409
        source,target,third,cid=s['source'],s['target'],s['third'],s['cid']
    backup(root,out,'accounts',confirm_stopped=True);restore(out,restored)
    with app_at(restored) as (api,state),TestClient(api.app) as a:
        login(a,0);url=f'/api/projects/{source}/conversations/{cid}'
        rejected=expect(a.get(url+'/move-requests/fenced'));assert rejected['move_state']=='not_executed'
        body={'target_project_id':target,'expected_token':old['token'],'request_id':'fenced','acknowledge_history':True}
        assert a.post(url+'/move',json=body).json()['detail']==rejected
        pre=expect(a.get(url+'/move-precheck',params={'target_project_id':third}));assert pre['can_move']
        result=expect(a.post(url+'/move',json={**body,'request_id':'user-selected-another-target','target_project_id':third,'expected_token':pre['token']}))
        assert result['project_id']==third and state['guidance_calls']==0
