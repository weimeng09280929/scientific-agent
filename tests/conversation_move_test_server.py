"""Only disposable accounts/files and offline model edges; never a product port."""
import argparse
from pathlib import Path
import tempfile
from fastapi.testclient import TestClient
from tests.conversation_move_fixture import fixture
from tests.local_backup_fixture import app_at,login,expect
from tests.project_overview_test_server import snapshot


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=8098);args=p.parse_args()
    root=args.root.resolve()
    if args.port in (8001,8002,8077) or root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-move-'):
        raise ValueError('Requires a fresh temporary root and isolated loopback port')
    root.mkdir(exist_ok=False)
    with fixture(root) as s:
        sample={k:s[k] for k in ('source','target','third','foreign','cid')}
        source=s['source'];a=s['a']
        for key,title in (('complex','关联草案，不能移动'),('lost','响应丢失恢复示例'),('stale','版本变化示例'),
                          ('review_refresh','拒绝后刷新恢复'),('review_inflight','原提交仍可能执行'),
                          ('review_proof','过时拒绝回执核对'),('review_refusal_lost','拒绝响应丢失'),
                          ('review_relocated','其他请求已经移动')):
            cid=expect(a.post(f'/api/projects/{source}/conversations',json={'request_id':key,'title':title}))['conversation']['conversation_id']
            expect(a.post(f'/api/projects/{source}/conversations/{cid}/messages',json={'request_id':key,'content':'这是一段普通文字讨论，请解释如何确认研究范围。','wait_for_completion':True}))
            sample[key]=cid
        from core.workspace.schema import Suggestion
        from core.workspace.conversations import ConversationService
        service=ConversationService(source,s['api']._CONFIG.paths.project_db(source))
        with service._connection(True) as c:
            proposal=Suggestion(kind='note',title='尚未采纳的关联建议',content='不能丢弃关联来移动',based_on_revision=1)
            c.execute('INSERT INTO sra_chat_suggestions VALUES (?,?,?,?,?,?)',(proposal.suggestion_id,source,sample['complex'],'complex',None,proposal.model_dump_json()))
        sample['seed_offline_calls']=s['state']['guidance_calls']
    (root/'selection.pdf').write_bytes(b'%PDF-1.4\n% Unsubmitted test selection; never uploaded')
    with app_at(root) as (api,state):
        import uvicorn
        baseline=snapshot(api)
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=5))
        @api.app.get('/__move__/state')
        def view():return {'sample':sample,'root':str(root),'snapshot':snapshot(api),'baseline':baseline,'calls':state}
        @api.app.post('/__move__/shutdown')
        def shutdown():server.should_exit=True;return {'stopping':True}
        print('Disposable move instance on loopback port '+str(args.port),flush=True)
        server.run()


if __name__=='__main__':main()
