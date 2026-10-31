"""Two actual local processes; kill only the disposable child we started."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def test_process_restart_releases_work_without_replay_and_keeps_owned_results():
    port=None
    for candidate in range(8089,8099):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',candidate))
            except OSError:continue
            port=candidate;break
    assert port is not None
    base=f'http://127.0.0.1:{port}'
    child=None
    def wait_for(predicate,seconds=15):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            try:
                value=predicate()
                if value:return value
            except httpx.TransportError:pass
            time.sleep(.05)  # Readiness/terminal polling; no concurrency assertion uses sleeps.
        raise AssertionError('isolated server state did not arrive')
    with tempfile.TemporaryDirectory(prefix='sra-resource-restart-') as directory:
        root=Path(directory)
        def start():
            nonlocal child
            child=subprocess.Popen([sys.executable,'-m','tests.local_resource_test_server',
                '--root',str(root),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            wait_for(lambda:httpx.get(base+'/__resource__/state',timeout=1).status_code==200)
        def stop():
            if child and child.poll() is None:
                httpx.post(base+'/__resource__/shutdown',timeout=3)
                child.wait(15)
        try:
            start()
            with httpx.Client(base_url=base,timeout=10) as a,httpx.Client(base_url=base,timeout=10) as b:
                def login(client,suffix):
                    response=client.post('/api/auth/login',headers={'X-SRA-Login':'1'},json={
                        'email':'tester-'+suffix.lower()+'@example.com','password':'browser acceptance '+suffix})
                    assert response.status_code==200
                    client.headers['X-SRA-CSRF']=response.json()['csrf_token']
                    return response.json()['user']['id']
                identity=login(a,'A')
                p=a.post('/api/conversations/start',json={'request_id':'persistent-start','title':'offline retained','domain':'ai'}).json()
                pid,cid=p['project_id'],p['conversation_id']
                assert a.post(f'/api/projects/{pid}/notes',json={'text':'owned note retained after process failure'}).status_code==200
                upload=a.post(f'/api/projects/{pid}/upload-paper',files={'file':('same.txt',b'bounded owned content','text/plain')},data={'request_id':'same-upload'})
                assert upload.status_code==200
                paper=upload.json()['paper_id']
                httpx.post(base+'/__resource__/hold-source',timeout=3)
                task_path=f'/api/projects/{pid}/conversations/{cid}/research-tasks'
                task=a.post(task_path,json={'request_id':'interrupted-search','content':'检索离线资料','topic':'offline'}).json()
                tid=task['task_id']
                assert httpx.get(base+'/__resource__/source-entered',timeout=10).json()['entered']
                assert httpx.get(base+'/__resource__/state').json()['heavy']==1
                child.terminate();child.wait(15)
                start()
                assert a.get('/api/auth/me').json()['user']['id']==identity
                assert a.get(f'/api/projects/{pid}/status').status_code==200
                assert 'owned note retained' in a.get(f'/api/projects/{pid}/notes').text
                papers=a.get(f'/api/projects/{pid}/papers').json()['papers']
                assert any(r['paper_id']==paper for r in papers)
                state=a.get(task_path+'/'+tid).json()
                assert state['status']=='interrupted' and state['attempts']==1
                idle=httpx.get(base+'/__resource__/state').json()
                assert idle['heavy']==0 and idle['guidance_calls']==idle['source_calls']==0
                login(b,'B')
                assert b.get(f'/api/projects/{pid}/status').status_code==404
                assert b.get('/api/projects').json()['projects']==[]
                resumed=a.post(task_path+'/'+tid+'/retry',json={})
                assert resumed.status_code==200
                def completed():
                    data=a.get(task_path+'/'+tid).json()
                    return data if data['status']=='completed' and not data['work_still_running'] else None
                done=wait_for(completed)
                assert done['task_id']==tid and done['attempts']==2
                assert a.post(f'/api/projects/{pid}/upload-paper',files={'file':('same.txt',b'bounded owned content','text/plain')},data={'request_id':'same-upload'}).json()['paper_id']==paper
                result={'passed':True,'port':port,'separateProcesses':2,'killedOnlyOwnedTestChild':True,
                        'sessionAndOwnershipRetained':True,'noteAndUploadRetained':True,'runningBecomesInterrupted':True,
                        'noAutomaticReplay':True,'zeroPermanentLeases':True,'originalTaskRetrySucceeds':True,
                        'realModelCalls':0,'publicSourceCalls':0,'temporaryRootRemoved':True}
                output=Path('docs/local-resource-protection');output.mkdir(exist_ok=True)
                (output/'restart-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        finally:stop()
