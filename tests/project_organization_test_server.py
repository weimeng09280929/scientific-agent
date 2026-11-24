"""Temporary project folders and independent conversation samples; offline only."""
import argparse
import json
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from tests.local_backup_fixture import app_at, login, expect
from tests.project_overview_test_server import seed, snapshot


def prepare(api):
    sample=seed(api)
    from core.workspace.conversations import ConversationService
    from core.workspace.schema import Message
    with TestClient(api.app) as client:
        login(client,0)
        first=sample['users'][0]
        second=first['empty_project_id']
        expect(client.patch('/api/projects/'+second+'/identity',json={'display_name':'人工智能RAG'}))
        blank=expect(client.post('/api/projects',json={'topic':'空文件夹','domain':'ai'}))['project_id']
        for pid,count in ((first['project_id'],7),(second,3)):
            for index in range(count):
                title='这是一段需要完整辨认的长名称对话' * 6 if index==count-1 else '同名对话'
                c=expect(client.post(f'/api/projects/{pid}/conversations',json={'title':title,'request_id':'folder-'+str(index)}))['conversation']
                chat=ConversationService(pid,api._CONFIG.paths.project_db(pid))
                with chat._connection(True) as conn:
                    chat._insert_message(conn,c['conversation_id'],Message(role='user',content=f'独立历史 {pid[-6:]} / {index}，不能显示为其他对话正文。',area='overview',mode='user').model_dump(mode='json'))
        sample['organization']={'first_project':first['project_id'],'second_project':second,'blank_project':blank}
        for key,pid in list(sample['organization'].items()):
            sample['organization'][key+'_conversations']=expect(client.get(f'/api/projects/{pid}/conversations?limit=100'))['conversations']
    return sample


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,default=8097);args=parser.parse_args()
    root=args.root.resolve()
    if args.port in (8001,8002,8077) or root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-organization-'):
        raise ValueError('Only an isolated temporary root and non-product port are allowed')
    root.mkdir(exist_ok=False)
    from core.local_accounts import AccountStore
    AccountStore(root/'accounts.db').initialize()
    with app_at(root) as (api,state):
        sample=prepare(api);baseline=snapshot(api);initial=dict(state)
        (root/'selection.pdf').write_bytes(b'%PDF-1.4\n% unsubmitted folder navigation fixture')
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=5))
        @api.app.get('/__organization__/state')
        def fixture_state():
            return {'sample':sample,'root':str(root),'snapshot':snapshot(api),'baseline':baseline,'initial_calls':initial,'calls':state}
        @api.app.post('/__organization__/shutdown')
        def shutdown():
            server.should_exit=True;return {'stopping':True}
        print('Isolated organization: http://127.0.0.1:'+str(args.port),flush=True)
        server.run()


if __name__=='__main__':main()
