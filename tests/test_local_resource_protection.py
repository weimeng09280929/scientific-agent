"""Actual FastAPI, isolated accounts/SQLite; all workers use event boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import threading

import pytest

from tests.test_local_account_isolation import isolated, _login
from core.resource_limits import RESOURCES, ACTOR, ResourceGovernor, ResourceLimits, CapacityError


@pytest.fixture
def env(isolated, monkeypatch):
    root, store, a, b, pa, pb = isolated
    monkeypatch.setattr(RESOURCES, "limits", replace(ResourceLimits(), heavy_global=2,
        file_bytes=512, body_bytes=1024, field_bytes=128, header_bytes=256, spool_bytes=32, chunk_bytes=32))
    _login(a, "tester-a@example.com", pa)
    _login(b, "tester-b@example.com", pb)
    from web import api
    yield root, a, b, api
    assert not any(RESOURCES.snapshot().values()), "test worker or receive lease leaked"


def project(client, n="same"):
    r=client.post('/api/projects',json={"topic": n,"domain":"ai"})
    assert r.status_code==200, r.text
    pid=r.json()['project_id']
    r=client.post(f'/api/projects/{pid}/conversations',json={"title":"same"})
    assert r.status_code==200, r.text
    return pid,r.json()['conversation']['conversation_id']


def messages(p):
    return f'/api/projects/{p[0]}/conversations/{p[1]}/messages'


def block_guidance(monkeypatch, *, fail=False):
    entered, release = threading.Event(), threading.Event()
    calls=[]
    from core.workspace.schema import GuideDraft
    def generate(*args):
        calls.append(1);entered.set()
        assert release.wait(15), 'test barrier not released'
        if fail:
            raise TimeoutError('offline controlled timeout')
        return GuideDraft(reply='离线验证说明，待用户进一步核对。')
    monkeypatch.setattr('web.workspace_api.generate_guidance',generate)
    return entered,release,calls


def test_same_user_across_projects_duplicate_and_rejected_request_recovers(env,monkeypatch):
    root,a,b,api=env
    p1,p2=project(a),project(a)
    entered,release,calls=block_guidance(monkeypatch)
    body={"content":"请解释研究范围","request_id":"same-id"}
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(a.post,messages(p1),json=body)
        try:
            assert entered.wait(8)
            duplicate=a.post(messages(p1),json=body)
            assert duplicate.status_code==202 and duplicate.json()['status']=='processing'
            rejected=a.post(messages(p2),json=body)
            assert rejected.status_code==429 and rejected.json()['reason_code']=='account_busy'
            assert sum(RESOURCES.snapshot()['heavy'].values())==1 and len(calls)==1
            state=a.get(messages(p2).replace('/messages','/requests/same-id'))
            assert state.status_code==404
            import sqlite3
            with closing(sqlite3.connect(api._CONFIG.paths.project_db(p2[0]))) as conn:
                tables={r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
                if 'sra_call_audit' in tables:
                    assert conn.execute('select count(*) from sra_call_audit').fetchone()[0]==0
        finally:
            release.set()
        assert future.result(10).status_code==200
    result=a.post(messages(p2),json=body)
    assert result.status_code==200 and len(calls)==2
    assert a.post(messages(p2),json=body).status_code==200 and len(calls)==2


def test_global_limit_two_actors_read_and_download_unaffected(env,monkeypatch,isolated):
    root,a,b,api=env
    p1,p2,p3=project(a),project(b),project(b)
    from core.knowledge import KnowledgeStore, Paper
    pdf=api._CONFIG.paths.project_dir(p2[0])/'existing.pdf';pdf.write_bytes(b'%PDF existing owned download')
    KnowledgeStore(api._CONFIG.paths.project_db(p2[0])).save_paper(Paper(paper_id='existing',title='existing',pdf_path=str(pdf)))
    monkeypatch.setattr(RESOURCES,'limits',replace(RESOURCES.limits,heavy_user=2))
    barrier=threading.Barrier(3);release=threading.Event();calls=[]
    from core.workspace.schema import GuideDraft
    def generate(*args):
        calls.append(1);barrier.wait(10);assert release.wait(15)
        return GuideDraft(reply='离线并发验证。')
    monkeypatch.setattr('web.workspace_api.generate_guidance',generate)
    with ThreadPoolExecutor(2) as pool:
        fa=pool.submit(a.post,messages(p1),json={"content":"说明范围","request_id":"same"})
        fb=pool.submit(b.post,messages(p2),json={"content":"说明范围","request_id":"same"})
        try:
            barrier.wait(10)
            r=b.post(messages(p3),json={"content":"说明范围","request_id":"third"})
            assert r.status_code==429 and r.json()['reason_code']=='system_busy'
            assert sum(RESOURCES.snapshot()['heavy'].values())==2 and len(calls)==2
            assert b.get('/api/projects').status_code==200
            assert b.get(f'/api/projects/{p2[0]}/notes').status_code==200
            download=b.get(f'/api/projects/{p2[0]}/papers/existing/pdf')
            assert download.status_code==200 and download.content==pdf.read_bytes()
            from fastapi.testclient import TestClient
            with TestClient(api.app) as fresh:
                _login(fresh,'tester-b@example.com',isolated[5])
                assert fresh.get(f'/api/projects/{p2[0]}/papers/existing/pdf').content==pdf.read_bytes()
            assert b.get(f'/api/projects/{p1[0]}/status').status_code==404
        finally:
            release.set()
        assert fa.result(10).status_code==200 and fb.result(10).status_code==200


def test_cancel_does_not_release_underlying_research_slot_or_retry_it(env,monkeypatch):
    root,a,b,api=env
    p1,p2=project(a),project(a)
    entered,release,done=threading.Event(),threading.Event(),threading.Event()
    count=[]
    def source(*args,**kwargs):
        count.append(1);entered.set();assert release.wait(15)
        return []
    from core.research_tasks import sources
    from core.research_tasks.service import ResearchTaskService
    monkeypatch.setattr(sources,'SOURCE_FUNCS',{k:source for k in sources.SOURCE_FUNCS})
    original=ResearchTaskService._execute
    def execute(*args,**kwargs):
        try:return original(*args,**kwargs)
        finally:done.set()
    monkeypatch.setattr(ResearchTaskService,'_execute',execute)
    base=messages(p1).replace('/messages','/research-tasks')
    r=a.post(base,json={"content":"检索测试","topic":"offline","request_id":"search"})
    assert r.status_code==200,r.text
    tid=r.json()['task_id']
    try:
        assert entered.wait(8)
        for _ in range(3):
            cancelled=a.post(f'{base}/{tid}/cancel')
            assert cancelled.json()['cancel_requested'] and cancelled.json()['work_still_running']
            assert '尚未结束' in cancelled.json()['cancel_note']
            assert a.post(f'{base}/{tid}/retry',json={}).status_code==200
        blocked=a.post(messages(p2),json={"content":"说明范围","request_id":"after-cancel"})
        assert blocked.status_code==429 and len(count)==1
        assert sum(RESOURCES.snapshot()['heavy'].values())==1
    finally:
        release.set()
    assert done.wait(10)
    # Event is raised inside execute's finally, before the outer lease closes;
    # a join proves actual worker completion without a timing sleep.
    for thread in threading.enumerate():
        if thread.name.startswith('Thread-') and thread.daemon:
            thread.join(1)
    state=a.get(f'{base}/{tid}').json()
    assert state['status']=='cancelled' and not state['report']
    assert not RESOURCES.snapshot()['heavy']


@pytest.mark.parametrize('route',['run','run-topic-discovery','regenerate-method-doc','workspace/guide','papers/import'])
def test_legacy_heavy_routes_cannot_bypass_busy_user(env,route):
    root,a,b,api=env
    p=project(a)
    user=a.get('/api/auth/me').json()['user']['id']
    token=ACTOR.set(user)
    try:lease=RESOURCES.acquire()
    finally:ACTOR.reset(token)
    bodies={'run':{},'run-topic-discovery':{'interest':'offline'},'regenerate-method-doc':{},
            'workspace/guide':{'message':'说明范围','area':'overview','request_id':'old',
                'expected_revision':a.get(f'/api/projects/{p[0]}/workspace').json()['workspace']['revision']},'papers/import':{'title':'offline'}}
    try:
        r=a.post(f'/api/projects/{p[0]}/{route}',json=bodies[route])
        assert r.status_code==429, (route,r.text)
        assert r.json()['reason_code']=='account_busy'
        assert api._PROJECTS[p[0]].thread is None
    finally:lease.close()


def test_timeout_failure_retains_slot_until_return_then_can_retry(env,monkeypatch):
    root,a,b,api=env;p=project(a)
    entered,release,calls=block_guidance(monkeypatch,fail=True)
    body={'content':'解释范围','request_id':'timeout'}
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(a.post,messages(p),json=body)
        try:
            assert entered.wait(8)
            assert sum(RESOURCES.snapshot()['heavy'].values())==1
        finally:release.set()
        assert f.result(10).status_code==502
    assert not RESOURCES.snapshot()['heavy']
    block_guidance(monkeypatch)[1].set()
    r=a.post(messages(p).replace('/messages','/requests/timeout/retry'),json={})
    assert r.status_code==200,r.text


@pytest.mark.parametrize('endpoint',['upload-paper','upload-topic'])
def test_upload_exact_limit_over_limit_and_no_partial_publication(env,endpoint):
    root,a,b,api=env;p=project(a);url=f'/api/projects/{p[0]}/{endpoint}'
    schema=api.app.openapi()['paths'][f'/api/projects/{{project_id}}/{endpoint}']['post']['requestBody']['content']['multipart/form-data']['schema']
    assert schema['required']==['file'] and schema['properties']['file']['format']=='binary'
    ok=a.post(url,files={'file':('same.txt',b'x'*512,'text/plain')})
    assert ok.status_code==200,ok.text
    before=a.get(f'/api/projects/{p[0]}/papers').json()
    over=a.post(url,files={'file':('same.txt',b'x'*513,'text/plain')})
    assert over.status_code==413 and '限制' in over.json()['detail']
    assert a.get(f'/api/projects/{p[0]}/papers').json()==before
    assert not list(api._CONFIG.paths.project_dir(p[0]).rglob('*.part'))


def multipart(data,boundary='test-resource',fields=b''):
    return (b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="file"; filename="same.txt"\r\n\r\n'+data+b'\r\n'+fields+b'--'+boundary.encode()+b'--\r\n')


@pytest.mark.parametrize('fault',['no-length','wrong-length','body','fields','files','header','incomplete','disconnect','timeout','malformed','many-fields'])
def test_multipart_actual_bytes_and_parser_handles_cleanup(env,monkeypatch,fault):
    root,a,b,api=env;p=project(a);url=f'/api/projects/{p[0]}/upload-paper'
    from web import bounded_uploads
    handles=[];original=bounded_uploads.BoundedMultipart.on_headers_finished
    def on_headers(self):
        try:return original(self)
        finally:handles[:]=self._files_to_close_on_error[:]
    monkeypatch.setattr(bounded_uploads.BoundedMultipart,'on_headers_finished',on_headers)
    raw=multipart(b'x'*513)
    if fault=='fields':raw=multipart(b'valid',fields=b'--test-resource\r\nContent-Disposition: form-data; name="title"\r\n\r\n'+b'x'*129+b'\r\n')
    if fault=='files':raw=multipart(b'valid',fields=b'--test-resource\r\nContent-Disposition: form-data; name="file"; filename="two.txt"\r\n\r\nvalid\r\n')
    if fault=='header':raw=multipart(b'valid').replace(b'same.txt',b'x'*300+b'.txt')
    if fault=='body':raw=b' '*1025
    if fault=='many-fields':raw=multipart(b'valid',fields=b''.join(b'--test-resource\r\nContent-Disposition: form-data; name="'+k+b'"\r\n\r\nx\r\n' for k in (b'a',b'b',b'c')))
    if fault in ('incomplete','disconnect','timeout'):raw=multipart(b'valid')[:-22]
    def chunks():
        for i in range(0,len(raw),17):yield raw[i:i+17]
        if fault=='disconnect':raise RuntimeError('offline client disconnect')
    headers={'Content-Type':'multipart/form-data; boundary=test-resource'}
    if fault=='malformed':headers['Content-Type']='multipart/form-data';raw=b'broken'
    if fault=='wrong-length':headers['Content-Length']='1'
    if fault in ('disconnect','timeout'):
        # Exercise the parser itself for a receive failure (TestClient rethrows
        # transport stream errors); still uses real Starlette multipart files.
        from starlette.requests import Request
        sent=False
        async def receive():
            nonlocal sent
            if not sent:sent=True;return {'type':'http.request','body':raw,'more_body':True}
            if fault=='timeout':await asyncio.Event().wait()
            return {'type':'http.disconnect'}
        async def run():
            req=Request({'type':'http','method':'POST','path':url,'headers':[(b'content-type',headers['Content-Type'].encode())]},receive)
            if fault=='timeout':monkeypatch.setattr(RESOURCES,'limits',replace(RESOURCES.limits,receive_seconds=1))
            with pytest.raises(Exception) as failure:
                async with bounded_uploads.receive_upload(req):pass
            if fault=='timeout':assert failure.value.status_code==408
        asyncio.run(run())
    else:
        result=a.post(url,content=chunks(),headers=headers)
        assert result.status_code in (400,413),result.text
    if fault in ('disconnect','timeout','incomplete','no-length','wrong-length','fields','files','many-fields'):
        assert handles, 'the test must actually create a parser-owned temporary file'
    assert all(h.closed for h in handles)
    assert a.get(f'/api/projects/{p[0]}/papers').json()['papers']==[]


def test_upload_parse_failure_removes_only_own_pdf_and_receipt_recovers(env,monkeypatch):
    root,a,b,api=env;p=project(a);url=f'/api/projects/{p[0]}/upload-paper'
    upload_dir=api._CONFIG.paths.project_dir(p[0])/'uploads';upload_dir.mkdir()
    old=upload_dir/'existing.pdf';old.write_bytes(b'original attachment')
    import core.tools
    monkeypatch.setattr(core.tools,'mineru_is_available',lambda:True)
    monkeypatch.setattr(core.tools,'parse_pdf_with_mineru',lambda *a:(_ for _ in ()).throw(ValueError('offline parse failure')))
    body={'request_id':'parse-failure'}
    result=a.post(url,files={'file':('same.pdf',b'%PDF offline','application/pdf')},data=body)
    assert result.status_code==422 and '解析' in result.json()['detail']
    assert list(upload_dir.iterdir())==[old] and old.read_bytes()==b'original attachment'
    assert a.get(f'/api/projects/{p[0]}/papers').json()['papers']==[]
    from types import SimpleNamespace
    monkeypatch.setattr(core.tools,'parse_pdf_with_mineru',lambda *a:SimpleNamespace(
        mode='fixture',sections=[],figures=[],title='',full_text='Offline readable content',tables=[],equations=[],references=[]))
    recovered=a.post(url,files={'file':('same.pdf',b'%PDF offline','application/pdf')},data=body)
    assert recovered.status_code==200,recovered.text
    duplicate=a.post(url,files={'file':('same.pdf',b'%PDF offline','application/pdf')},data=body)
    assert duplicate.json()['paper_id']==recovered.json()['paper_id'] and duplicate.json()['already_uploaded']
    assert old.read_bytes()==b'original attachment'


def test_research_worker_start_failure_and_original_task_retry(env,monkeypatch):
    root,a,b,api=env;p=project(a)
    from core.research_tasks.service import ResearchTaskService
    original=ResearchTaskService._launch
    def fail_runner(self,*args):
        self._runner=lambda fn:(_ for _ in ()).throw(RuntimeError('offline start failed'))
        return original(self,*args)
    monkeypatch.setattr(ResearchTaskService,'_launch',fail_runner)
    base=messages(p).replace('/messages','/research-tasks')
    body={'content':'检索','topic':'offline','request_id':'worker'}
    assert a.post(base,json=body).status_code==503
    same=a.post(base,json=body)
    assert same.status_code==200 and same.json()['task']['status']=='failed'
    assert not RESOURCES.snapshot()['heavy']
    monkeypatch.setattr(ResearchTaskService,'_launch',original)
    tid=same.json()['task_id']
    result=a.post(f'{base}/{tid}/retry',json={})
    assert result.status_code==200
    # Join only the actual research worker launched by this test.
    for thread in threading.enumerate():
        if thread.name.startswith('Thread-') and thread.daemon:thread.join(10)
    assert not RESOURCES.snapshot()['heavy']


def test_atomic_admission_and_fresh_process_has_no_permanent_leases():
    governor=ResourceGovernor(ResourceLimits(heavy_user=2,heavy_global=3))
    barrier=threading.Barrier(9);release=threading.Event();settled=threading.Event();entered=[]
    outcome_lock=threading.Lock();outcomes=[]
    def attempt(index):
        token=ACTOR.set('actor-'+str(index%2));barrier.wait(10)
        try:
            lease=None
            try:lease=governor.acquire()
            except CapacityError:pass
            with outcome_lock:
                outcomes.append(lease is not None)
                if lease:entered.append(index)
                if len(outcomes)==8:settled.set()
            if lease:
                with lease:assert release.wait(10)
        finally:ACTOR.reset(token)
    with ThreadPoolExecutor(8) as pool:
        futures=[pool.submit(attempt,i) for i in range(8)];barrier.wait(10)
        try:
            assert settled.wait(8)
            assert len(entered)==3 and len(outcomes)==8
            counts=governor.snapshot()['heavy']
            assert sum(counts.values())==3 and max(counts.values())<=2
        finally:release.set()
        for f in futures:f.result(10)
    assert not any(governor.snapshot().values())
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c',"from core.resource_limits import RESOURCES; assert not any(RESOURCES.snapshot().values())"],capture_output=True)
    assert result.returncode==0


@pytest.mark.parametrize('action',['retry','restart'])
def test_p1_recovery_entrances_reject_before_claim_and_keep_original_budget(env,monkeypatch,action):
    root,a,b,api=env;p=project(a)
    from core.invocation_audit import CallLimitError
    from core.workspace.schema import GuideDraft
    def fail(*args):
        if action=='restart':raise CallLimitError('offline exhausted')
        raise TimeoutError('offline timeout')
    monkeypatch.setattr('web.workspace_api.generate_guidance',fail)
    first=a.post(messages(p),json={'content':'解释范围','request_id':'p1'}).json()
    assert first['status']=='failed'
    url=messages(p).replace('/messages','/requests/p1')
    before=a.get(url).json()
    user=a.get('/api/auth/me').json()['user']['id'];token=ACTOR.set(user)
    try:lease=RESOURCES.acquire()
    finally:ACTOR.reset(token)
    try:
        r=a.post(url+'/'+action,json={'acknowledge_model_cost':True} if action=='restart' else {})
        assert r.status_code==429,r.text
        after=a.get(url).json()
        assert after['attempt']==before['attempt'] and after['call_audit']==before['call_audit']
        assert after['recovery_request_id'] is None
    finally:lease.close()
    monkeypatch.setattr('web.workspace_api.generate_guidance',lambda *args:GuideDraft(reply='恢复验证成功。'))
    result=a.post(url+'/'+action,json={'acknowledge_model_cost':True} if action=='restart' else {})
    assert result.status_code==200,result.text


def test_upload_receiving_admission_before_any_body_read_and_disconnect_cleanup(env,monkeypatch):
    root,a,b,api=env;pa,pb=project(a),project(b)
    monkeypatch.setattr(RESOURCES,'limits',replace(RESOURCES.limits,receive_user=1))
    async def run():
        entered_a,entered_b=asyncio.Event(),asyncio.Event()
        release=asyncio.Event()
        def request(client,p,entered):
            output=[];reads=[]
            async def receive():
                reads.append(1);entered.set();await release.wait()
                return {'type':'http.disconnect'}
            async def send(message):output.append(message)
            headers=[(b'host',b'testserver'),(b'content-type',b'multipart/form-data; boundary=test-resource'),
                     (b'cookie',('sra_session='+client.cookies.get('sra_session')).encode()),
                     (b'x-sra-csrf',client.headers['X-SRA-CSRF'].encode())]
            scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST',
                   'path':f'/api/projects/{p[0]}/upload-paper','raw_path':b'/',
                   'query_string':b'','scheme':'http','headers':headers,
                   'client':('testclient',1234),'server':('testserver',80)}
            return asyncio.create_task(api.app(scope,receive,send)),output,reads
        ta,oa,ra=request(a,pa,entered_a)
        await asyncio.wait_for(entered_a.wait(),5)
        duplicate,od,rd=request(a,pa,asyncio.Event())
        try:
            await asyncio.wait_for(duplicate,5)
            status=next(m['status'] for m in od if m['type']=='http.response.start')
            assert status==429 and not rd
            tb,ob,rb=request(b,pb,entered_b)
            await asyncio.wait_for(entered_b.wait(),5)
            assert sum(RESOURCES.snapshot()['receive'].values())==2
            assert not RESOURCES.snapshot()['heavy']
        finally:release.set()
        # A disconnect may be propagated by ASGI; no parser files or leases survive.
        await asyncio.gather(ta,tb,return_exceptions=True)
    asyncio.run(run())
    assert not any(RESOURCES.snapshot().values())


def test_upload_duplicate_does_not_parse_again_and_busy_rejection_keeps_original_id(env,monkeypatch):
    root,a,b,api=env;pa,pb=project(a),project(b)
    entered,release=threading.Event(),threading.Event();calls=[];read_sizes=[]
    original=api._prepare_upload
    def prepare(pid,*args):
        calls.append(pid)
        if pid==pa[0]:entered.set();assert release.wait(10)
        return original(pid,*args)
    monkeypatch.setattr(api,'_prepare_upload',prepare)
    from starlette.datastructures import UploadFile
    read=UploadFile.read
    async def bounded_read(self,size=-1):read_sizes.append(size);return await read(self,size)
    monkeypatch.setattr(UploadFile,'read',bounded_read)
    url=f'/api/projects/{pa[0]}/upload-paper'
    body={'request_id':'same-upload'};files={'file':('same.txt',b'content is bounded','text/plain')}
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(a.post,url,files=files,data=body)
        try:
            assert entered.wait(8)
            duplicate=a.post(url,files=files,data=body)
            assert duplicate.status_code==202 and duplicate.json()['status']=='processing'
            blocked=a.post(url,files=files,data={'request_id':'next-upload'})
            assert blocked.status_code==429
            assert a.get(f'/api/projects/{pa[0]}/upload-requests/next-upload').status_code==404
            assert calls==[pa[0]] and sum(RESOURCES.snapshot()['heavy'].values())==1
            other=b.post(f'/api/projects/{pb[0]}/upload-paper',files=files,data=body)
            assert other.status_code==200,other.text
        finally:release.set()
        first=f.result(10)
    assert first.status_code==200
    again=a.post(url,files=files,data=body)
    assert again.json()['paper_id']==first.json()['paper_id'] and calls.count(pa[0])==1
    second=a.post(url,files=files,data={'request_id':'next-upload'})
    assert second.status_code==200 and second.json()['paper_id']!=first.json()['paper_id']
    assert read_sizes and all(size==32 for size in read_sizes)
    assert len(a.get(f'/api/projects/{pa[0]}/papers').json()['papers'])==2


@pytest.mark.parametrize('route',['run','run-topic-discovery','regenerate-method-doc'])
def test_legacy_thread_start_failure_releases_admission(env,monkeypatch,route):
    root,a,b,api=env;p=project(a)
    original=threading.Thread
    class CannotStart(original):
        def start(self):
            if getattr(self._target,'__name__','')=='run':
                raise RuntimeError('offline worker start failure')
            return super().start()
    monkeypatch.setattr(api.threading,'Thread',CannotStart)
    r=a.post(f'/api/projects/{p[0]}/{route}',json={'interest':'offline'} if route=='run-topic-discovery' else {})
    assert r.status_code==503 and '未能启动' in r.json()['detail']
    assert not RESOURCES.snapshot()['heavy']


@pytest.mark.parametrize('format',['pdf','docx'])
def test_format_conversion_uses_heavy_slot_while_saved_markdown_download_does_not(env,format):
    root,a,b,api=env;p=project(a)
    user=a.get('/api/auth/me').json()['user']['id'];token=ACTOR.set(user)
    try:lease=RESOURCES.acquire()
    finally:ACTOR.reset(token)
    try:
        base=f'/api/projects/{p[0]}/download/research-report'
        assert a.get(base).status_code==200
        r=a.get(base,params={'format':format})
        assert r.status_code==429 and r.json()['reason_code']=='account_busy'
    finally:lease.close()


def test_cancelled_http_upload_keeps_actual_parser_slot_until_worker_returns(env,monkeypatch):
    root,a,b,api=env;p=project(a)
    entered,release,closed=threading.Event(),threading.Event(),threading.Event();calls=[]
    original=api._prepare_upload
    def prepare(*args):
        calls.append(1);entered.set();assert release.wait(15)
        return original(*args)
    monkeypatch.setattr(api,'_prepare_upload',prepare)
    from core.resource_limits import Lease
    close=Lease.close
    def observe(lease):
        close(lease)
        if lease.category=='heavy':closed.set()
    monkeypatch.setattr(Lease,'close',observe)
    async def run():
        raw=multipart(b'bounded complete body');sent=False
        async def receive():
            nonlocal sent
            if not sent:sent=True;return {'type':'http.request','body':raw,'more_body':False}
            await asyncio.Event().wait()
        async def send(message):pass
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST',
            'path':f'/api/projects/{p[0]}/upload-paper','raw_path':b'/','query_string':b'','scheme':'http',
            'headers':[(b'host',b'testserver'),(b'content-type',b'multipart/form-data; boundary=test-resource'),
                (b'cookie',('sra_session='+a.cookies.get('sra_session')).encode()),
                (b'x-sra-csrf',a.headers['X-SRA-CSRF'].encode())],
            'client':('testclient',123),'server':('testserver',80)}
        task=asyncio.create_task(api.app(scope,receive,send))
        try:
            assert await asyncio.to_thread(entered.wait,8)
            task.cancel()
            r=await asyncio.to_thread(a.post,f'/api/projects/{p[0]}/upload-paper',
                files={'file':('other.txt',b'another','text/plain')},data={'request_id':'other'})
            assert r.status_code==429 and len(calls)==1
            assert sum(RESOURCES.snapshot()['heavy'].values())==1
        finally:release.set()
        assert await asyncio.to_thread(closed.wait,10)
        await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())
    assert len(a.get(f'/api/projects/{p[0]}/papers').json()['papers'])==1


@pytest.mark.parametrize('size',[2048,2049])
def test_legacy_remote_pdf_ingress_reads_bounded_chunks_and_closes_response(env,monkeypatch,size):
    root,a,b,api=env;p=project(a)
    monkeypatch.setattr(RESOURCES,'limits',replace(RESOURCES.limits,file_bytes=2048,body_bytes=4096))
    from core.knowledge import KnowledgeStore,Paper
    KnowledgeStore(api._CONFIG.paths.project_db(p[0])).save_paper(Paper(paper_id='remote',title='offline remote file',metadata={'pdf_url':'https://fixture.invalid/file.pdf'}))
    raw=b'%PDF'+b'x'*(size-4);state={'closed':False,'read':0,'calls':0}
    class Response:
        @property
        def content(self):raise AssertionError('whole remote body must not be read')
        def raise_for_status(self):pass
        def iter_content(self,chunk_size):
            assert chunk_size==32
            for i in range(0,len(raw),chunk_size):
                chunk=raw[i:i+chunk_size];state['read']+=len(chunk);yield chunk
        def close(self):state['closed']=True
    def get(*args,**kwargs):state['calls']+=1;assert kwargs['stream'];return Response()
    monkeypatch.setattr('requests.get',get)
    r=a.get(f'/api/projects/{p[0]}/papers/remote/pdf')
    assert r.status_code==(200 if size==2048 else 413),r.text[:100]
    assert state['closed'] and state['calls']==1 and state['read']<=2080
    if size==2048:assert r.content==raw
    else:assert not list(api._CONFIG.paths.project_dir(p[0]).glob('papers/*.pdf'))
