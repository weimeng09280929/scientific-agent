"""Offline-only, disposable account/data instance for resource UI acceptance."""
import argparse
import os
from pathlib import Path
import threading
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path)
    parser.add_argument('--port',type=int,default=8088)
    args=parser.parse_args()
    if args.port in (8001,8002,8077):raise ValueError('Reserved real service port')
    from contextlib import nullcontext
    context=nullcontext(str(args.root.resolve())) if args.root else tempfile.TemporaryDirectory(prefix='sra-resource-browser-')
    with context as directory:
        root=Path(directory)
        if root.resolve().parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-resource-'):
            raise ValueError('Test root must be an explicit sra-resource-* child of the OS temporary directory')
        os.environ.update({'SRA_AUTH_MODE':'accounts','SRA_AUTH_INSECURE_HTTP':'1',
            'SRA_DATA_ROOT':str(root),'SRA_PROJECTS_DIR':str(root/'projects'),'SRA_ACCOUNTS_DB':str(root/'accounts.db'),
            'SRA_WEB_SKIP_GUARD':'1','SRA_WEB_SKIP_PROJECT_SCAN':'1','SRA_DRY_RUN':'true',
            'SRA_ALLOW_LOCAL_REGISTRATION':'0',
            'SRA_RESOURCE_HEAVY_USER':'1','SRA_RESOURCE_HEAVY_GLOBAL':'1',
            'SRA_RESOURCE_FILE_BYTES':'512','SRA_RESOURCE_BODY_BYTES':'2048',
            'SRA_RESOURCE_SPOOL_BYTES':'32','SRA_RESOURCE_CHUNK_BYTES':'32'})
        from core.local_accounts import AccountStore
        from contextlib import closing
        store=AccountStore(root/'accounts.db')
        if not store.path.exists():store.initialize()
        for suffix in ('a','b'):
            with closing(store._connect()) as conn:
                exists=conn.execute('SELECT 1 FROM users WHERE email=?',('tester-'+suffix+'@example.com',)).fetchone()
            if not exists:store.create_user('tester-'+suffix+'@example.com','browser acceptance '+suffix.upper())
        from core.config import get_config
        from tests.local_account_fixture import install
        state=install(get_config())
        from web import api
        from core.resource_limits import RESOURCES
        from core.workspace.schema import GuideDraft
        import web.workspace_api
        entered,release=threading.Event(),threading.Event();release.set()
        source_entered,source_release=threading.Event(),threading.Event();source_release.set()
        state.update(guidance_calls=0,active_guidance=0,peak_guidance=0)
        state_lock=threading.Lock()
        def guidance(*args):
            with state_lock:
                state['guidance_calls']+=1;state['active_guidance']+=1
                state['peak_guidance']=max(state['peak_guidance'],state['active_guidance'])
            entered.set()
            try:
                if not release.wait(90):raise TimeoutError('offline barrier timeout')
                return GuideDraft(reply='离线资源验收回复：请进一步核对范围和资料。')
            finally:
                with state_lock:state['active_guidance']-=1
        web.workspace_api.generate_guidance=guidance
        from core.research_tasks import sources
        def controlled_source(original):
            def call(*args,**kwargs):
                source_entered.set()
                if not source_release.wait(90):raise TimeoutError('offline source barrier timeout')
                return original(*args,**kwargs)
            return call
        sources.SOURCE_FUNCS={k:controlled_source(fn) for k,fn in sources.SOURCE_FUNCS.items()}
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=args.port,log_level='warning'))
        @api.app.get('/__resource__/state')
        def status():
            with state_lock:result=dict(state)
            return {**result,'heavy':sum(RESOURCES.snapshot()['heavy'].values()),
                    'receive':sum(RESOURCES.snapshot()['receive'].values()),'root':str(root)}
        @api.app.post('/__resource__/hold')
        def hold():entered.clear();release.clear();return {'holding':True}
        @api.app.get('/__resource__/entered')
        def wait_entered():return {'entered':entered.wait(8)}
        @api.app.post('/__resource__/release')
        def resume():release.set();return {'released':True}
        @api.app.post('/__resource__/hold-source')
        def hold_source():source_entered.clear();source_release.clear();return {'holding':True}
        @api.app.get('/__resource__/source-entered')
        def wait_source():return {'entered':source_entered.wait(8)}
        @api.app.post('/__resource__/release-source')
        def resume_source():source_release.set();return {'released':True}
        @api.app.post('/__resource__/shutdown')
        def shutdown():release.set();source_release.set();server.should_exit=True;return {'stopping':True}
        print('Isolated resource acceptance: http://127.0.0.1:'+str(args.port),flush=True)
        server.run()


if __name__=='__main__':main()
