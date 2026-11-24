"""Disposable accounts and read-only overview samples. No external calls."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from fastapi.testclient import TestClient
from tests.local_backup_fixture import app_at, EMAILS, PASSWORDS, login, expect


def seed(api):
    from core.local_accounts import AccountStore
    from core.knowledge import KnowledgeStore
    from core.knowledge.schema import Paper
    root = api._CONFIG.paths.root
    accounts = AccountStore(root / "accounts.db")
    accounts.initialize()
    for email, password in zip(EMAILS, PASSWORDS):
        accounts.create_user(email, password.get_secret_value())
    sample = {"users": []}
    with TestClient(api.app) as a, TestClient(api.app) as b:
        for index, client in enumerate((a, b)):
            login(client, index)
            result = expect(client.post('/api/conversations/start', json={
                'request_id': 'overview-start', 'title': '人工智能RAG' if index == 0 else 'B私有研究', 'domain': 'ai'}))
            pid, cid = result['project_id'], result['conversation_id']
            base = f'/api/projects/{pid}'
            store = KnowledgeStore(api._CONFIG.paths.project_db(pid))
            store.save_paper(Paper(paper_id='overview-paper', title='RAG 离线资料（未阅读全文）', abstract='Offline excerpt'))
            revision = expect(client.get(base+'/workspace'))['workspace']['revision']
            workspace = expect(client.put(base+'/workspace/brief', json={
                'expected_revision': revision, 'brief': {'goal': '研究检索增强生成的资料依据与回答可靠性。', 'resources': '只有 CPU，没有 GPU', 'time_budget': '四周'}}))['workspace']
            question = expect(client.post(base+'/workspace/items', json={'expected_revision': workspace['revision'],
                'item': {'kind': 'question', 'title': 'RAG 的证据条件如何影响回答可靠性？', 'content': '比较检索依据与回答可靠性，尚未执行。', 'paper_ids': ['overview-paper']}}))['workspace']['items'][-1]
            chat = base + f'/conversations/{cid}'
            draft = expect(client.post(chat+'/messages', json={'request_id': 'initial-plan', 'content': '请生成计算研究方案',
                'plan_request': {'method': 'computational', 'question_id': question['item_id']}, 'wait_for_completion': True}))['suggestions'][0]
            formal = expect(client.post(chat+f"/suggestions/{draft['suggestion_id']}/apply", json={}))['item']
            pending = expect(client.post(chat+'/messages', json={'request_id': 'draft-plan', 'content': '请修订当前计算研究方案',
                'plan_request': {'method': 'computational', 'supersedes_id': formal['item_id']}, 'wait_for_completion': True}))['suggestions'][0]
            expect(client.post(base+'/notes', json={'text': '已保存的阅读笔记；不能从聊天自动推定。'}))
            empty = expect(client.post('/api/projects', json={'topic': '空项目' if index == 0 else 'B空项目', 'domain': 'ai'}))['project_id']
            sample['users'].append({**result, 'empty_project_id': empty, 'question_id': question['item_id'],
                                    'plan_id': formal['item_id'], 'draft_id': pending['suggestion_id']})
    return sample


def snapshot(api):
    result = {}
    for db in api._CONFIG.paths.projects.glob('*/knowledge.db'):
        with closing(sqlite3.connect(db)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {name: conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in
                      ('sra_conversations', 'sra_chat_messages', 'sra_chat_requests', 'sra_chat_suggestions', 'sra_call_audit') if name in tables}
        result[db.parent.name] = {'sha256': hashlib.sha256(db.read_bytes()).hexdigest(), 'counts': counts}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8096)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.port in (8001, 8002, 8077) or root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-overview-'):
        raise ValueError('Only an isolated temporary root and non-product port are allowed')
    root.mkdir(exist_ok=False)
    from core.local_accounts import AccountStore
    AccountStore(root/'accounts.db').initialize()
    with app_at(root) as (api, state):
        sample = seed(api)
        baseline = snapshot(api)
        initial_calls = dict(state)
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(api.app, host='127.0.0.1', port=args.port, log_level='warning'))
        @api.app.get('/__overview__/state')
        def fixture_state():
            return {'sample': sample, 'snapshot': snapshot(api), 'baseline': baseline,
                    'initial_calls': initial_calls, 'calls': state, 'root': str(root)}
        @api.app.post('/__overview__/shutdown')
        def shutdown():
            server.should_exit = True
            return {'stopping': True}
        (root/'selection.pdf').write_bytes(b'%PDF-1.4\n% unsubmitted selection fixture')
        print('Isolated overview: http://127.0.0.1:'+str(args.port), flush=True)
        server.run()


if __name__ == '__main__':
    main()
