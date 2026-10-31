"""Disposable real website, with the provider boundary captured offline."""
import argparse
import json
from pathlib import Path
import tempfile
from fastapi.testclient import TestClient
from tests.research_partner_fixture import application,create,send,workspace
from tests.test_project_background import change
from tests.local_backup_fixture import login,expect


def seed(api,state,calls,control):
    from core.knowledge import KnowledgeStore
    from core.knowledge.schema import Paper
    result={}
    with TestClient(api.app) as a,TestClient(api.app) as b:
        login(a,0);login(b,1)
        for key,title,client in [('full','背景复用验收课题',a),('empty','空背景课题',a),('other','另一个已有课题',a),('foreign','B 的私有课题',b)]:
            pid=expect(client.post('/api/projects',json={'topic':title,'domain':'ai'}))['project_id']
            w=expect(client.get(f'/api/projects/{pid}/workspace'))['workspace']
            expect(client.put(f'/api/projects/{pid}/workspace/brief',json={'expected_revision':w['revision'],'brief':{}}))
            cid=expect(client.post(f'/api/projects/{pid}/conversations',json={'title':'背景验收对话'}))['conversation']['conversation_id']
            result[key]={'pid':pid,'cid':cid}
        s={'a':a,'api':api,'pid':result['full']['pid']}
        change(s,goal='比较检索依据与回答可靠性',background='已学 Python 入门',resources='只有 CPU，没有 GPU',time_budget='四周')
        store=KnowledgeStore(api._CONFIG.paths.project_db(s['pid']))
        store.save_paper(Paper(paper_id='offline-background',title='背景验收离线资料',abstract='只作软件验收，不提供真实科研结论。'))
        w=workspace(s)
        q=expect(a.post(f"/api/projects/{s['pid']}/workspace/items",json={'expected_revision':w['revision'],'item':{
            'kind':'question','title':'检索依据如何影响回答可靠性？','content':'比较两种检索方法的依据。','paper_ids':['offline-background']}}))['workspace']['items'][-1]
        cid=result['full']['cid']
        p=expect(send(s,cid,'请生成计算研究方案','initial-plan',plan_request={'method':'computational','question_id':q['item_id']}))['suggestions'][0]
        formal=expect(a.post(f"/api/projects/{s['pid']}/conversations/{cid}/suggestions/{p['suggestion_id']}/apply",json={}))['item']
        expect(send(s,cid,'请修订当前计算研究方案','pending-plan',plan_request={'method':'computational','supersedes_id':formal['item_id']}))
        result['full']['question_id']=q['item_id'];result['full']['plan_id']=formal['item_id']
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,default=8098);args=parser.parse_args()
    root=args.root.resolve()
    if args.port in (8001,8002,8077) or root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-background-'):raise ValueError('Isolated root and port required')
    root.mkdir(exist_ok=False)
    with application(root) as (api,state,calls,control):
        sample=seed(api,state,calls,control);seed_calls=len(calls);calls.clear()
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=5))
        @api.app.get('/__background__/state')
        def current():return {'sample':sample,'calls':calls,'state':state,'seed_offline_calls':seed_calls,'root':str(root)}
        @api.app.post('/__background__/shutdown')
        def shutdown():server.should_exit=True;return {'stopping':True}
        (root/'selection.pdf').write_bytes(b'%PDF-1.4\n% unsubmitted background test selection')
        (root/'fixture.json').write_text(json.dumps(sample),encoding='utf-8')
        print('Isolated background instance on 127.0.0.1:'+str(args.port),flush=True)
        try:server.run()
        finally:(root/'stopped.txt').write_text('isolated fixture stopped',encoding='utf-8')


if __name__=='__main__':main()
