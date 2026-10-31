from contextlib import closing
import json
import sqlite3
import hashlib
import pytest

from tests.conversation_move_fixture import fixture
from tests.local_backup_fixture import expect
from tests.project_overview_test_server import snapshot
from core.workspace.conversation_moves import ConversationMover,ConversationMoved
from core.workspace.conversations import ConversationService,ConversationStorageError
from core.workspace.service import WorkspaceError,RevisionConflict
from core.workspace.conversation_schema import ConversationSend


@pytest.fixture
def sample(tmp_path):
    with fixture(tmp_path) as data:yield data


def base(s,pid=None):return f"/api/projects/{pid or s['source']}/conversations/{s['cid']}"
def check(s,target=None):return expect(s['a'].get(base(s)+'/move-precheck',params={'target_project_id':target or s['target']}))
def move(s,token=None,rid='move-1',target=None):
    return s['a'].post(base(s)+'/move',json={'target_project_id':target or s['target'],'request_id':rid,'expected_token':token or check(s)['token'],'acknowledge_history':True})
def material(s,pid):return expect(s['a'].get(f'/api/projects/{pid}/workspace?include_chat=false'))['workspace']


def business_snapshot(s):
    """Only the new refusal table may change on an explicit rejected POST."""
    from core.workspace.conversation_moves import REFUSALS
    result={}
    for db in s['api']._CONFIG.paths.projects.glob('*/knowledge.db'):
        with sqlite3.connect(db) as conn:
            values={}
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if name==REFUSALS:continue
                rows=conn.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall()
                values[name]=hashlib.sha256(json.dumps(sorted(rows,key=repr),ensure_ascii=False).encode()).hexdigest()
            result[db.parent.name]=values
    return result


def test_atomic_move_identity_history_budget_and_target_context(sample):
    s=sample;a=s['a'];before=snapshot(s['api']);calls=dict(s['state'])
    original=expect(a.get(base(s)+'/messages'))['messages'];old_meta=expect(a.get(base(s)))['conversation']
    with sqlite3.connect(s['api']._CONFIG.paths.project_db(s['source'])) as c:raw=c.execute('SELECT message_id,data FROM sra_chat_messages ORDER BY seq').fetchall()
    wsource,wtarget=material(s,s['source']),material(s,s['target'])
    pre=check(s);assert pre['can_move'] and pre['reasons']==[]
    assert snapshot(s['api'])==before and s['state']==calls
    result=expect(move(s,pre['token']));assert result['project_id']==s['target']
    assert expect(a.get(f"/api/projects/{s['source']}/conversations"))['conversations']==[]
    rows=expect(a.get(f"/api/projects/{s['target']}/conversations"))['conversations'];assert len(rows)==1 and rows[0]['conversation_id']==s['cid']
    moved_meta=expect(a.get(base(s,s['target'])))['conversation']
    for field in ('conversation_id','title','created_at','updated_at','chat_revision','create_request_id'):assert moved_meta[field]==old_meta[field]
    moved=expect(a.get(base(s,s['target'])+'/messages'))['messages']
    # Local pagination seq may change; persistent message IDs, JSON, order and times do not.
    def project(rows):
        return [{**{k:v for k,v in r.items() if k not in ('seq','actions','historical_project_context','history_project_id','history_project_name')},'actions':[{k:v for k,v in a.items() if k not in ('enabled','disabled_reason')} for a in r.get('actions',[])]} for r in rows]
    assert project(moved)==project(original)
    with sqlite3.connect(s['api']._CONFIG.paths.project_db(s['target'])) as c:assert c.execute('SELECT message_id,data FROM sra_chat_messages ORDER BY seq').fetchall()==raw
    assert all(not action['enabled'] for m in moved for action in m['actions'])
    assert all(m['history_project_id']==s['source'] for m in moved)
    assert material(s,s['source'])==wsource and material(s,s['target'])==wtarget
    assert expect(move(s,pre['token']))['move_id']==result['move_id']
    assert expect(a.get(base(s)+'/move-requests/move-1'))['move_id']==result['move_id']
    old=a.post('/api/conversations/start',json=s['start_body']);assert old.status_code==409 and old.json()['detail']['code']=='conversation_moved'
    oldsend=a.post(base(s)+'/messages',json={'request_id':'late-old-tab','content':'旧标签页禁止重建'});assert oldsend.status_code==409
    call_count=s['state']['guidance_calls']
    replay=a.post(base(s,s['target'])+'/messages',json={'request_id':'text-0','content':'只有 CPU，没有 GPU，请先解释怎样讨论研究范围。'})
    assert replay.status_code==200 and s['state']['guidance_calls']==call_count
    old_audit=expect(a.get(base(s,s['target'])+'/requests/text-0'))['call_audit']['entries'];assert sum(r['event']=='network' for r in old_audit)==1
    expect(a.post(base(s,s['target'])+'/messages',json={'request_id':'new-discussion','content':'移动后请解释如何核对条件，不生成方案。','wait_for_completion':True}))
    observed=s['observed'][-1];assert '已有一块 GPU' in observed['brief']['resources']
    assert not observed['history'] and len(observed['conversation']['prior_project_history'])==4
    assert all(r['origin_project_id']==s['source'] for r in observed['conversation']['prior_project_history'])
    assert material(s,s['target'])==wtarget


@pytest.mark.parametrize('kind',['paper','selected','suggestion','processing','upper_budget','audit_parent','unknown_relation'])
def test_complex_or_unknown_relationships_block_without_writes(sample,kind):
    s=sample;db=s['api']._CONFIG.paths.project_db(s['source'])
    with sqlite3.connect(db) as c,c:
        if kind=='paper':
            row=c.execute('SELECT seq,data FROM sra_chat_messages LIMIT 1').fetchone();d=json.loads(row[1]);d['cited_paper_ids']=['paper-linked'];c.execute('UPDATE sra_chat_messages SET data=? WHERE seq=?',(json.dumps(d),row[0]))
        elif kind=='selected':
            r=c.execute('SELECT request_id,payload FROM sra_chat_requests LIMIT 1').fetchone();d=json.loads(r[1]);d['selected_paper_ids']=['attachment'];c.execute('UPDATE sra_chat_requests SET payload=? WHERE request_id=?',(json.dumps(d),r[0]))
        elif kind=='suggestion':
            from core.workspace.schema import Suggestion
            p=Suggestion(based_on_revision=1,kind='note',title='关联草案',content='不能丢弃')
            c.execute('INSERT INTO sra_chat_suggestions VALUES (?,?,?,?,?,?)',(p.suggestion_id,s['source'],s['cid'],'text-0',None,p.model_dump_json()))
        elif kind=='processing':c.execute("UPDATE sra_chat_requests SET status='processing' WHERE request_id='text-0'")
        elif kind=='upper_budget':c.execute("UPDATE sra_call_audit SET group_id='upper-group' WHERE event='network'")
        elif kind=='audit_parent':c.execute("UPDATE sra_call_audit SET parent_id='other-conversation-audit' WHERE event='network'")
        elif kind=='unknown_relation':
            c.execute('CREATE TABLE future_relation (conversation_id TEXT,data TEXT)');c.execute('INSERT INTO future_relation VALUES (?,?)',(s['cid'],'must not discard'))
    before=snapshot(s['api']);business=business_snapshot(s);result=check(s);assert not result['can_move'] and result['reasons']
    assert snapshot(s['api'])==before  # precheck remains completely read only
    assert move(s,result['token']).status_code==422
    assert business_snapshot(s)==business
    outcome=expect(s['a'].get(base(s)+'/move-requests/move-1'))
    assert outcome['move_state']=='not_executed' and outcome['expected_token']==result['token']


def test_revision_and_account_checks(sample):
    s=sample;pre=check(s)
    w=material(s,s['target']);expect(s['a'].put(f"/api/projects/{s['target']}/workspace/brief",json={'expected_revision':w['revision'],'brief':{'goal':'另一标签页修改条件'}}))
    business=business_snapshot(s);assert move(s,pre['token']).status_code==409
    assert business_snapshot(s)==business
    assert check(s,s['source'])['can_move'] is False
    assert s['a'].get(base(s)+'/move-precheck',params={'target_project_id':s['foreign']}).status_code==404
    assert s['b'].get(base(s)+'/location').status_code==404
    assert s['b'].post(base(s)+'/move',json={'request_id':'evil','target_project_id':s['foreign'],'expected_token':pre['token'],'acknowledge_history':True}).status_code==404


@pytest.mark.parametrize('phase',['conversation_copied','records_copied','source_removed'])
def test_faults_rollback_both_databases_then_original_request_can_retry(sample,phase):
    s=sample;paths=s['api']._CONFIG.paths;service=ConversationMover(paths,s['api']._require_project)
    # Direct service uses the same trusted actor context through the API only;
    # the fault harness supplies a local existence check for its disposable IDs.
    service.require_project=lambda pid: paths.project_db(pid).is_file() or (_ for _ in ()).throw(WorkspaceError('missing'))
    service.check_owner=service.require_project
    pre=service.precheck(s['source'],s['target'],s['cid']);before=snapshot(s['api'])
    def fail(where):
        if where==phase:raise OSError('offline injected storage failure')
    with pytest.raises(ConversationStorageError) as failed:service.confirm(s['source'],s['target'],s['cid'],'fault-move',pre['token'],fault=fail)
    assert isinstance(failed.value.__cause__,OSError)
    # SQLite header/page bookkeeping may change on rollback; business rows must not.
    after=snapshot(s['api']);assert {p:r['counts'] for p,r in after.items()}=={p:r['counts'] for p,r in before.items()}
    assert expect(s['a'].get(base(s)))['conversation']['conversation_id']==s['cid']
    assert expect(s['a'].get(f"/api/projects/{s['target']}/conversations"))['conversations']==[]
    assert expect(move(s,pre['token'],rid='fault-move'))['project_id']==s['target']


def test_running_send_blocks_move_until_actual_exit(sample):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import patch
    import web.workspace_api
    s=sample;entered,release=Event(),Event();original=web.workspace_api.generate_guidance
    def held(*args,**kwargs):
        entered.set();assert release.wait(15);return original(*args,**kwargs)
    pre=check(s)
    with patch('web.workspace_api.generate_guidance',held),ThreadPoolExecutor(1) as pool:
        job=pool.submit(s['a'].post,base(s)+'/messages',json={'request_id':'held-send','content':'普通文字讨论','wait_for_completion':True})
        try:
            assert entered.wait(10)
            assert not check(s)['can_move']
            assert move(s,pre['token']).status_code==409
        finally:release.set()
        expect(job.result(15))
    assert move(s,pre['token']).status_code==409  # changed history, not silent acceptance
    assert expect(move(s))['project_id']==s['target']


def test_concurrent_duplicate_confirm_and_other_target_identity(sample):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    s=sample;pre=check(s);barrier=Barrier(2)
    def confirm():barrier.wait(10);return move(s,pre['token'])
    with ThreadPoolExecutor(2) as pool:
        jobs=[pool.submit(confirm) for _ in range(2)];results=[expect(j.result(20)) for j in jobs]
    assert results[0]['move_id']==results[1]['move_id']
    assert move(s,pre['token'],target=s['third']).status_code==409
    assert len(expect(s['a'].get(f"/api/projects/{s['target']}/conversations"))['conversations'])==1


def test_failed_history_cannot_retry_restart_or_activate_old_card(sample):
    s=sample;s['state']['fault']='timeout'
    failed=expect(s['a'].post(base(s)+'/messages',json={'request_id':'failed-text','content':'请解释范围'}),502)
    assert failed['status']=='failed';s['state']['fault']=None
    calls=dict(s['state']);expect(move(s));target=base(s,s['target'])
    view=expect(s['a'].get(target+'/requests/failed-text'));assert view['failure']['reason']=='timeout' and view['failure']['recovery_blocked_reason']=='moved_history'
    assert not view['failure']['can_retry'] and not view['failure']['can_restart']
    assert s['a'].post(target+'/requests/failed-text/retry',json={}).json()['attempt']==1
    assert s['a'].post(target+'/requests/failed-text/restart',json={'acknowledge_model_cost':True}).status_code==409
    messages=expect(s['a'].get(target+'/messages'))['messages']
    card=next((m,a) for m in messages for a in m['actions'] if a['type']=='start_search')
    r=s['a'].post(target+'/research-tasks',json={'request_id':'old-card','topic':'offline research','content':'检索','card_message_id':card[0]['message_id'],'card_action_id':card[1]['action_id']})
    assert r.status_code==409 and '历史卡片' in r.text
    assert s['state']==calls


def test_multi_hop_and_return_preserve_old_links_and_creation_identity(sample):
    s=sample;a=s['a'];expect(move(s));first=s['source'];second=s['target'];third=s['third']
    def again(source,target,rid):
        url=base(s,source);pre=expect(a.get(url+'/move-precheck',params={'target_project_id':target}));assert pre['can_move']
        return expect(a.post(url+'/move',json={'target_project_id':target,'expected_token':pre['token'],'request_id':rid,'acknowledge_history':True}))
    again(second,third,'second-move')
    for pid in (first,second):assert expect(a.get(base(s,pid)+'/location'))['project_id']==third
    create_id=expect(a.get(base(s,third)))['conversation']['create_request_id']
    assert a.post(f'/api/projects/{first}/conversations',json={'title':'不可重建','request_id':create_id}).status_code==409
    again(third,first,'return-move')
    for pid in (first,second,third):assert expect(a.get(base(s,pid)+'/location'))['project_id']==first
    assert expect(a.post('/api/conversations/start',json=s['start_body']))['conversation_id']==s['cid']
    assert len(expect(a.get(base(s)))['conversation']['movement_history'])==3


def test_crash_recovers_both_databases_without_task_replay(tmp_path):
    import subprocess,sys
    from core.workspace.conversation_moves import recover_journals
    with fixture(tmp_path) as s:
        source,target,cid=s['source'],s['target'],s['cid'];paths=s['api']._CONFIG.paths
        pre=check(s);before=snapshot(s['api'])
    script='''
import os,sys
from pathlib import Path
from core.workspace.conversation_moves import ConversationMover
from core.config import ProjectPaths
root,source,target,cid,token=sys.argv[1:]
paths=ProjectPaths.from_root(root)
m=ConversationMover(paths,lambda pid: None)
original=m._connect
def connect(*args):
 c=original(*args);c.execute('PRAGMA main.cache_size=1');c.execute('PRAGMA destination.cache_size=1');return c
m._connect=connect
m.confirm(source,target,cid,'crash-move',token,fault=lambda phase: os._exit(71) if phase=='source_removed' else None)
'''
    p=subprocess.run([sys.executable,'-c',script,str(tmp_path),source,target,cid,pre['token']],capture_output=True,text=True)
    assert p.returncode==71,p.stderr
    assert recover_journals(paths)>=1
    after=snapshot(s['api']);assert {p:r['counts'] for p,r in before.items()}=={p:r['counts'] for p,r in after.items()}
    m=ConversationMover(paths,lambda pid: None)
    assert m.confirm(source,target,cid,'crash-move',pre['token'])['project_id']==target


def test_backup_restore_moved_ownership_starts_history_and_sessions(tmp_path):
    from core.local_backup import backup,verify,restore,digest,inventory,BackupError
    from tests.local_backup_fixture import app_at,login
    from fastapi.testclient import TestClient
    source=tmp_path/'source';out=tmp_path/'backup';target=tmp_path/'restored'
    with fixture(source) as s:
        expect(move(s));pid,cid,original=s['target'],s['cid'],s['source']
        old_cookie=s['a'].cookies.get('sra_session');start=s['start_body'];before=expect(s['a'].get(base(s,pid)+'/messages'))
    assert backup(source,out,'accounts',confirm_stopped=True)['ok']
    manifest=verify(out);assert manifest['storage_contract']==2
    source_before=inventory(source,manifest['layout'])
    assert restore(out,target)['old_sessions_removed']
    with app_at(target) as (api,state),TestClient(api.app) as a,TestClient(api.app) as b:
        a.cookies.set('sra_session',old_cookie);assert a.get('/api/projects').status_code==401;a.cookies.clear()
        login(a,0);login(b,1);url=f'/api/projects/{pid}/conversations/{cid}'
        assert expect(a.get(url+'/messages'))==before
        assert expect(a.get(f'/api/projects/{original}/conversations/{cid}/location'))['project_id']==pid
        assert a.post('/api/conversations/start',json=start).status_code==409
        assert b.get(url).status_code==404 and b.get(url+'/location').status_code==404
        assert state['guidance_calls']==0
        expect(a.post(url+'/messages',json={'request_id':'restored-new','content':'继续核对资料','wait_for_completion':True}))
        assert state['guidance_calls']==1
    assert inventory(source,manifest['layout'])==source_before
    # v1 cannot claim to understand relocation receipts, even with a valid seal.
    from core.local_backup import write_json
    manifest['storage_contract']=1;write_json(out/'manifest.json',manifest)
    (out/'COMPLETE').write_text(digest(out/'manifest.json'),encoding='ascii')
    with pytest.raises(BackupError,match='v2'):verify(out)


def test_same_name_chat_identity_is_not_merged_or_mis_selected(sample):
    s=sample;a=s['a']
    second=expect(a.post(f"/api/projects/{s['source']}/conversations",json={'title':'同名讨论','request_id':'same-title-different-chat'}))['conversation']['conversation_id']
    expect(a.post(f"/api/projects/{s['source']}/conversations/{second}/messages",json={'content':'同名但不同的历史内容','request_id':'text-0','wait_for_completion':True}))
    before=expect(a.get(f"/api/projects/{s['source']}/conversations/{second}/messages"))
    expect(move(s))
    remaining=expect(a.get(f"/api/projects/{s['source']}/conversations"))['conversations']
    assert [r['conversation_id'] for r in remaining]==[second]
    assert expect(a.get(f"/api/projects/{s['source']}/conversations/{second}/messages"))==before


def test_legacy_backup_contract_without_moves_remains_readable(tmp_path):
    from core.local_backup import backup,verify,restore,digest,write_json
    root=tmp_path/'source';out=tmp_path/'backup'
    with fixture(root):pass
    backup(root,out,'accounts',confirm_stopped=True);manifest=verify(out);manifest['storage_contract']=1
    write_json(out/'manifest.json',manifest);(out/'COMPLETE').write_text(digest(out/'manifest.json'),encoding='ascii')
    assert verify(out)['storage_contract']==1
    assert restore(out,tmp_path/'restored')['ok']


def test_backup_rejects_move_receipt_cross_account_ownership(tmp_path):
    from core.local_backup import backup,BackupError
    from core.workspace.conversation_moves import TABLE
    root=tmp_path/'source'
    with fixture(root) as s:expect(move(s))
    for pid in (s['source'],s['target']):
        with sqlite3.connect(s['api']._CONFIG.paths.project_db(pid)) as c,c:
            row=c.execute(f'SELECT move_id,data FROM {TABLE}').fetchone();data=json.loads(row[1]);data['target_project_id']=s['foreign']
            c.execute(f'UPDATE {TABLE} SET target_project_id=?,data=? WHERE move_id=?',(s['foreign'],json.dumps(data),row[0]))
    with pytest.raises(BackupError,match='同账号'):backup(root,tmp_path/'rejected','accounts',confirm_stopped=True)
    assert not (tmp_path/'rejected'/'COMPLETE').exists()
