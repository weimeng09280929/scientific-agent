"""Verified-quote repair is bounded, revalidated and never adoption."""
import pytest
from core.workspace.plans import rule_plan
from tests.research_partner_fixture import sample as fixture,create,send,workspace,brief
from tests.local_backup_fixture import expect

@pytest.mark.parametrize('case',['fixed','still_bad','downgrade','foreign_quote','gpu_conflict','bad_citation'])
def test_attribution_repair_and_old_formal_plan(tmp_path,case):
    with fixture(tmp_path) as s:
        brief(s,resources='只有 CPU，没有 GPU',time_budget='四周')
        cid=create(s);w=workspace(s)
        q=expect(s['a'].post(f"/api/projects/{s['pid']}/workspace/items",json={'expected_revision':w['revision'],'item':{'kind':'question','title':'明确的联邦学习比较问题','content':'比较联邦学习中的不同隐私风险。','paper_ids':['abstract-only']}}))['workspace']['items'][-1]
        p=expect(send(s,cid,'请生成计算研究方案','v1',plan_request={'method':'computational','question_id':q['item_id']}))['suggestions'][0]
        v1=expect(s['a'].post(f"/api/projects/{s['pid']}/conversations/{cid}/suggestions/{p['suggestion_id']}/apply",json={}))['item']
        before=workspace(s);initial_calls=len(s['calls'])
        def provider(ctx,text):
            draft=rule_plan(ctx);statement=draft.suggestions[0].plan.sections[0].statements[0]
            if 'condition_repair' not in ctx or case=='still_bad':statement.text+=' 并在方案中完成额外分析任务。'
            if case=='downgrade' and 'condition_repair' in ctx:statement.basis='proposal'
            if case=='foreign_quote':statement.quote='用户根本没有提供的条件'
            if case=='gpu_conflict':statement.text='用户已确认有GPU可供使用'
            if case=='bad_citation':statement.paper_ids=['foreign']
            return draft
        s['control']['result']=provider
        body={'method':'computational','supersedes_id':v1['item_id']}
        result=send(s,cid,'请修订当前计算研究方案','repair',plan_request=body)
        assert workspace(s)==before
        calls=s['calls'][initial_calls:]
        assert len(calls)==(1 if case in ('foreign_quote','gpu_conflict','bad_citation') else 2)
        if case=='fixed':
            r=expect(result);assert r['suggestions'][0]['applied_item_id'] is None
            assert r['suggestions'][0]['supersedes_id']==v1['item_id']
            assert calls[-1]['context']['condition_repair']['field']=='user_statement'
            assert any(e['name']=='condition_repair:user_statement' for e in r['call_audit']['entries'])
        else:
            assert result.status_code==422 and result.json()['failure']['reason']=='structure_invalid'
            assert not result.json()['failure']['can_retry'] and not result.json()['suggestions']
            count=len(s['calls'])
            retried=s['a'].post(f"/api/projects/{s['pid']}/conversations/{cid}/requests/repair/retry",json={})
            assert retried.status_code==422 and len(s['calls'])==count
