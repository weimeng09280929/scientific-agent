"""Temporary main-app acceptance. All retrieval/model boundaries are offline."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SRA_WEB_SKIP_GUARD'] = '1'
os.environ['SRA_WEB_SKIP_PROJECT_SCAN'] = '1'


def serve(root, port, baseline=False):
    import core.config as configuration
    cfg=configuration.GlobalConfig(paths=configuration.ProjectPaths.from_root(root),dry_run=False)
    configuration._default_config=cfg
    from core.research_tasks import sources
    from web import api
    import uvicorn
    if baseline:
        # Exact pre-fix published functions, ONLY inside this temporary fixture
        # process. Never restore/reset the developer's files or user databases.
        import ast
        import subprocess
        import core.research_tasks.service as service
        import core.research_tasks.synthesis as synthesis
        old_service = ast.parse(subprocess.check_output(['git','show','d92b37f:core/research_tasks/service.py']).decode('utf-8'))
        old_synthesis = ast.parse(subprocess.check_output(['git','show','d92b37f:core/research_tasks/synthesis.py']).decode('utf-8'))
        def original(node, scope):
            code=ast.Module(body=[node],type_ignores=[])
            exec(compile(code,'<published-pre-fix>','exec'),scope)
            return scope[node.name]
        renderer=next(n for n in old_synthesis.body if isinstance(n,ast.FunctionDef) and n.name=='report_markdown')
        service.report_markdown=original(renderer,dict(synthesis.__dict__))
        cls=next(n for n in old_service.body if isinstance(n,ast.ClassDef) and n.name=='ResearchTaskService')
        for node in cls.body:
            if isinstance(node,ast.FunctionDef) and node.name in ('view','_deliver','_delivery_text'):
                setattr(service.ResearchTaskService,node.name,original(node,dict(service.__dict__)))

    control = {'model_calls': [], 'mode': 'partial'}
    gate=threading.Event()

    class Registry:
        def complete(self, task_type, prompt=None, **kwargs):
            control['model_calls'].append(task_type)
            if task_type == 'research_plan':
                data = {'queries':[{'query':'offline federated learning comparison','rationale':'离线流程夹具'}]}
            elif task_type == 'research_rank':
                data = {'rankings':[]}
            else:
                if control['mode']=='hold_synthesis' and not gate.wait(20):
                    raise RuntimeError('offline synthesis gate timed out')
                if control['mode']=='bad_synthesis':
                    return SimpleNamespace(text='{"representative":')
                candidates = json.loads(prompt.split('候选文献：',1)[1].split('\n来源执行情况',1)[0])
                data = {'representative':[{'paper_id':c['paper_id'],'point':'离线摘要描述了一个可比较的研究对象；仅用于验证交付与引用，不是科学结论。'} for c in candidates[:2]],
                        'methods_relations':' '.join('paper:'+c['paper_id'] for c in candidates[:2])+' 的对象与条件需要分别核对，不能直接外推。',
                        'limitations':[{'point':'离线摘要没有提供实际比较结果，仍需核对原始材料。','paper_ids':[c['paper_id'] for c in candidates[:1]]}]}
                if control['mode']=='cross_reference':
                    a,b=[c['paper_id'] for c in candidates[:2]]
                    data={'representative':[{'paper_id':a,'point':'基于离线摘要，与 paper:'+b+' 的条件不同；需要核对原文。'}],
                          'methods_relations':'','limitations':[{'paper_ids':[a],'point':'还需核查 paper:'+b+' 的比较条件。'}]}
            return SimpleNamespace(text=json.dumps(data,ensure_ascii=False))

    from core.llm import LLMRegistry
    from core.workspace.service import WorkspaceService
    LLMRegistry.from_config=classmethod(lambda cls,config: Registry())
    app=api.app
    pid=api.create_project(api.CreateProjectRequest(topic='研究现状交付 · 离线夹具',domain='ai'))['project_id']
    WorkspaceService(pid,cfg.paths.project_db(pid))
    control['source_calls']=[]
    # Reuse only the already-verified PUBLIC bibliography for a metadata-only
    # sample. No original abstract, PDF or new literature claim is fabricated.
    public_sources=json.loads((Path(__file__).resolve().parents[1]/'content/teaching-cases/fl-heterogeneity/1.1.0.json').read_text(encoding='utf-8'))['sources'][:2]

    def source(name):
        def call(*args, **kwargs):
            control['source_calls'].append(name)
            if control['mode'] == 'partial' and name == 'arxiv':
                raise TimeoutError('offline source unavailable')
            class Paper:
                def to_meta_dict(self):
                    if control['mode']=='metadata':
                        p=public_sources[0 if name=='openalex' else 1]
                        return {'title':p['title'],'authors':p['authors'],'year':p['year'],'doi':p.get('doi'),
                                'url':p['url'],'arxiv_id':None,'abstract':None}
                    return {'title':'Fixture '+name+' study','authors':['Fixture Author'], 'year':2024,
                            'abstract':'Offline comparison of problem definitions and validation conditions. Not scientific evidence.',
                            'doi':'10.0000/'+name,'url':'https://fixture.invalid/'+name,'arxiv_id':None}
            return [Paper()]
        return call

    sources.SOURCE_FUNCS = {name:source(name) for name in ('openalex','arxiv')}
    import requests
    def forbid_external(*args,**kwargs):
        raise AssertionError('Public network is forbidden in offline acceptance')
    requests.sessions.Session.request=forbid_external
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))

    @app.get('/__delivery__/state')
    def state():
        return control

    @app.get('/__fixture__/state')
    def fixture_state():
        return {'project_id':pid}

    @app.post('/__delivery__/mode')
    def mode(body:dict):
        control['mode'] = body['mode']
        gate.clear()
        return control

    @app.post('/__delivery__/release')
    def release():
        gate.set()
        return {'released':True}

    @app.post('/__delivery__/shutdown')
    def shutdown():
        gate.set()
        server.should_exit = True
        return {'stopping':True}

    server.run()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8068)
    parser.add_argument('--baseline',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sra-delivery-') as root:
        serve(Path(root),args.port,args.baseline)
