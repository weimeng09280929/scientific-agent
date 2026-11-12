"""Atomic, same-owner moves of ordinary text conversations (rollback journals)."""
from contextlib import closing, ExitStack
import json
import re
import sqlite3
from uuid import uuid4

from core.workspace.conversations import ConversationService, ConversationNotFound, ConversationStorageError, DDL, encode
from core.workspace.service import WorkspaceError, RevisionConflict, KEY
from core.workspace.materials import fingerprint
from core.workspace.schema import now
from core.workspace import conversation_lock

TABLE='sra_conversation_moves'
REFUSALS='sra_conversation_move_refusals'
REFUSAL_SQL=f'''CREATE TABLE IF NOT EXISTS {REFUSALS} (
 conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, expected_token TEXT NOT NULL,
 target_project_id TEXT NOT NULL, status_code INTEGER NOT NULL, message TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(conversation_id,request_id,expected_token))'''
SQL=f'''CREATE TABLE IF NOT EXISTS {TABLE} (
 move_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 source_project_id TEXT NOT NULL, target_project_id TEXT NOT NULL,
 generation INTEGER NOT NULL, data TEXT NOT NULL,
 UNIQUE(source_project_id,conversation_id,request_id))'''
NOTICE='历史消息会保留。移动后，新的讨论使用目标项目的资料；原项目已确认的研究条件和成果不会因此成为目标项目的正式记录。'


class ConversationMoved(ConversationNotFound):
    def __init__(self, target, cid):
        super().__init__('对话已移动到其他项目，旧地址不能继续发送；请核对新的归属。')
        self.target, self.cid=target,cid


class MoveRefused(WorkspaceError):
    def __init__(self,result):
        super().__init__(result['message'])
        self.result=result


def tables(conn, schema='main'):
    return {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def history(conn,cid,schema='main'):
    if TABLE not in tables(conn,schema):return []
    return [json.loads(r[0]) for r in conn.execute(f'SELECT data FROM {schema}.{TABLE} WHERE conversation_id=? ORDER BY generation,move_id',(cid,))]


def moved_target(conn,pid,cid=None,create_id=None):
    if TABLE not in tables(conn):return None
    rows=conn.execute(f'SELECT data FROM {TABLE} WHERE source_project_id=? ORDER BY generation DESC',(pid,))
    for row in rows:
        data=json.loads(row[0])
        if cid==data['conversation_id'] or create_id is not None and data.get('create_request_id')==create_id:
            return data
    return None


def resolve(paths,pid,cid,require_project):
    visited=set()
    for _ in range(32):
        require_project(pid)
        if pid in visited:raise WorkspaceError('移动记录存在循环，未执行任何写入')
        visited.add(pid);db=paths.project_db(pid)
        with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            if 'sra_conversations' in tables(conn) and conn.execute('SELECT 1 FROM sra_conversations WHERE conversation_id=? AND project_id=?',(cid,pid)).fetchone():
                return pid
            record=moved_target(conn,pid,cid)
        if not record:raise ConversationNotFound('对话不存在或不属于当前项目')
        pid=record['target_project_id']
    raise WorkspaceError('移动链过长，暂不能安全解析归属')


class ConversationMover:
    def __init__(self,paths,require_project,check_owner=None):
        self.paths,self.require_project,self.check_owner=paths,require_project,check_owner or require_project

    def _paths(self,source,target):
        from core.instance_state import is_link
        for pid in (source,target):
            self.require_project(pid)
            db=self.paths.project_db(pid)
            if not db.is_file() or is_link(db) or is_link(db.parent) or db.stat().st_nlink!=1 or db.parent.resolve().parent!=self.paths.projects.resolve() or db.resolve().parent!=self.paths.project_dir(pid).resolve():
                raise ConversationNotFound('项目数据路径不可用')
    def _connect(self,source,target):
        self._paths(source,target)
        conn=sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=rw',uri=True,timeout=2)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute('ATTACH DATABASE ? AS destination',(self.paths.project_db(target).resolve().as_uri()+'?mode=rw',))
            conn.execute('PRAGMA main.synchronous=FULL');conn.execute('PRAGMA destination.synchronous=FULL')
            return conn
        except BaseException:conn.close();raise

    def _inspect(self,conn,source,target,cid):
        reasons=[]
        def block(code,text):
            if not any(r['code']==code for r in reasons):reasons.append({'code':code,'message':text})
        st,dt=tables(conn),tables(conn,'destination')
        row=conn.execute('SELECT * FROM sra_conversations WHERE project_id=? AND conversation_id=?',(source,cid)).fetchone() if 'sra_conversations' in st else None
        if not row:
            moved=moved_target(conn,source,cid)
            if moved:raise ConversationMoved(moved['target_project_id'],cid)
            raise ConversationNotFound('对话不存在或不是当前可移动的普通对话')
        meta=dict(row)
        migration=conn.execute("SELECT value FROM sra_chat_meta WHERE key='migration_version'").fetchone() if 'sra_chat_meta' in st else None
        if not migration or migration[0]!='1':
            block('schema','会话存储版本不兼容，本轮不在移动时升级历史库。')
        if source==target:block('same_project','当前项目不能作为移动目标。')
        if meta['is_legacy']:block('legacy','历史兼容对话仍关联项目级旧消息，本版暂不支持无损移动。')
        if meta.get('case_id') or meta.get('guidance_mode')=='case':block('teaching','教学案例对话不能作为普通文字对话移动。')
        for schema in ('main','destination'):
            if conn.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] not in ('delete','persist','truncate'):
                block('journal','项目数据库未使用可保证跨库原子提交的日志模式，暂不能移动。')
        payload={}
        allowed={'sra_conversations','sra_chat_messages','sra_chat_requests','sra_chat_failures','sra_call_audit',TABLE}
        for table in st:
            # Negative receipts fence old submissions at their original source;
            # they are not research relations and are never transferred.
            if table==REFUSALS:continue
            quoted='"'+table.replace('"','""')+'"'
            columns={r[1] for r in conn.execute(f'PRAGMA table_info({quoted})')}
            if 'conversation_id' not in columns or table=='sra_conversations':continue
            rows=[dict(r) for r in conn.execute(f'SELECT * FROM {quoted} WHERE conversation_id=? LIMIT 1001',(cid,))]
            if len(rows)>1000:block('too_large','本版每类历史记录最多移动 1000 条，超出时不截断搬迁。')
            payload[table]=rows
            if rows and table not in allowed:
                block('suggestions' if table=='sra_chat_suggestions' else 'research_tasks' if table=='sra_research_tasks' else 'other_relations',
                      '含科研建议、草案或已采纳成果关联，不能丢弃后移动。' if table=='sra_chat_suggestions' else '含检索任务、报告或其他科研关联，本版不能同步移动。')
        messages=sorted(payload.get('sra_chat_messages',[]),key=lambda r:r['seq'])
        if not messages:block('empty','尚无已保存消息；请勿通过移动创建空会话。')
        for m in messages:
            data=json.loads(m['data'])
            linked_actions=any(a.get('item_id') or a.get('suggestion_id') or a.get('paper_ids') or a.get('type') not in ('start_search','open_settings','view_references') for a in data.get('actions',[]))
            if linked_actions or any(data.get(k) for k in ('cited_paper_ids','suggestion_ids','research_task_id','report_citations','report_version','reading_materials')):
                block('references','历史消息含论文引用、结构化操作或报告／草案关联，本版不能保持这些依据后移动。')
        for request in payload.get('sra_chat_requests',[]):
            if request['status'] not in ('completed','failed','cancelled'):
                block('pending','存在正在执行或结果尚未确定的请求，请先按原请求恢复规则核对。')
            body=json.loads(request['payload'])
            if any(body.get(key) for key in ('selected_item_id','selected_paper_ids','plan_request','reading_guide','passage')):
                block('attachments','历史请求绑定论文、附件、正式材料或方案目标，本版不能同步移动。')
        audits=payload.get('sra_call_audit',[])
        audit_ids={a['id'] for a in audits}
        if any(a.get('parent_id') and a['parent_id'] not in audit_ids for a in audits):
            block('audit_relation','调用审计关联其他会话的父记录，本版不能拆分移动。')
        if any(a.get('group_id') not in (None,a['operation_id']) for a in audits):
            block('upper_budget','使用了跨请求的总调用／费用限制组，本版不能将其拆分或重置后移动。')
        if any(a.get('task_id') or a.get('kind')=='source' or a.get('outcome')=='processing' for a in audits):
            block('audit_pending','调用记录包含检索、任务关联或未确定的尝试，请先核对，不能当作普通文字记录移动。')
        if conversation_lock.is_held(self.paths.project_db(source),cid):block('working','底层操作仍持有执行权，请等待实际退出。')
        if 'sra_conversations' in dt and conn.execute('SELECT 1 FROM destination.sra_conversations WHERE conversation_id=? OR create_request_id=?',(cid,meta['create_request_id'])).fetchone():
            block('identity_conflict','目标项目已有相同会话或创建请求身份，不能覆盖或合并。')
        for table,idcol in (('sra_chat_messages','message_id'),('sra_call_audit','id')):
            if table in dt:
                for r in payload.get(table,[]):
                    if conn.execute(f'SELECT 1 FROM destination.{table} WHERE {idcol}=?',(r[idcol],)).fetchone():block('identity_conflict','目标项目已有相同历史记录身份，不能覆盖。')
        project_snapshots=[]
        for schema in ('main','destination'):
            kv=[tuple(r) for r in conn.execute(f'SELECT key,value FROM {schema}.kv_store ORDER BY key')]
            project_snapshots.append(kv)
            if any(k=='teaching.case.v1' for k,v in kv):block('teaching','来源或目标为教学项目，本版仅支持普通项目。')
        workspace=dict(project_snapshots[1]).get(KEY)
        target_migration=conn.execute("SELECT value FROM destination.sra_chat_meta WHERE key='migration_version'").fetchone() if 'sra_chat_meta' in dt else None
        if target_migration and target_migration[0]!='1':block('schema','目标会话存储版本不兼容，本轮不升级历史库。')
        if workspace and json.loads(workspace).get('messages') and not target_migration:
            block('legacy_target','目标存在尚未转换的旧历史对话，请先在原界面核对。')
        # Configuration-only metadata and saved brief are allowed, but token-bound.
        token=fingerprint({'meta':meta,'records':payload,'projects':project_snapshots})
        return {'can_move':not reasons,'reasons':reasons,'token':token,'conversation':meta,'notice':NOTICE},payload,project_snapshots

    def precheck(self,source,target,cid):
        # mode=ro for both databases: no schema setup, lock files, or receipts.
        self._paths(source,target)
        try:
            with closing(sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                conn.row_factory=sqlite3.Row
                conn.execute('ATTACH DATABASE ? AS destination',(self.paths.project_db(target).resolve().as_uri()+'?mode=ro',))
                result,_,_=self._inspect(conn,source,target,cid)
                result['target_project_id']=target
                return result
        except WorkspaceError:raise
        except (sqlite3.Error,ValueError,KeyError,TypeError) as exc:raise ConversationStorageError('移动预检查暂时不可读取或关联数据不完整，未修改数据') from exc

    def status(self,source,cid,rid):
        self.require_project(source)
        with closing(sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            if TABLE not in tables(conn):raise ConversationNotFound('该移动请求尚未完成或不存在')
            row=conn.execute(f'SELECT data FROM {TABLE} WHERE source_project_id=? AND conversation_id=? AND request_id=?',(source,cid,rid)).fetchone()
        if not row:raise ConversationNotFound('该移动请求尚未完成或不存在')
        data=json.loads(row[0]);target=resolve(self.paths,source,cid,self.require_project)
        return {'status':'completed','move_id':data['move_id'],'project_id':target,'conversation_id':cid,'source_project_id':source,'request_id':rid}

    def _refusal(self,conn,cid,rid,token=None):
        if REFUSALS not in tables(conn):return None
        sql=f'SELECT * FROM {REFUSALS} WHERE conversation_id=? AND request_id=?'
        args=[cid,rid]
        if token is not None:sql+=' AND expected_token=?';args.append(token)
        row=conn.execute(sql+' ORDER BY created_at DESC LIMIT 1',args).fetchone()
        return {**dict(row),'move_state':'not_executed','code':'move_rejected'} if row else None

    def outcome(self,source,cid,rid):
        try:return {**self.status(source,cid,rid),'move_state':'completed'}
        except ConversationNotFound:pass
        self.require_project(source)
        with closing(sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            rejected=self._refusal(conn,cid,rid)
        if rejected:return rejected
        raise ConversationNotFound('尚无最终回执；不能据此认定原提交未执行')

    def record_refusal(self,source,target,cid,rid,token,exc):
        """Called under the same API guard after confirm has exited/rolled back.

        Fence this exact attempted token before promising it cannot execute.
        A fresh explicit precheck may use the same rid with a different token.
        Storage/unknown outcomes must never be passed to this method.
        """
        self.require_project(source);self.require_project(target)
        with closing(sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=rw',uri=True)) as conn,conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            known=tables(conn)
            if TABLE in known and conn.execute(f'SELECT 1 FROM {TABLE} WHERE source_project_id=? AND conversation_id=? AND request_id=?',(source,cid,rid)).fetchone():
                raise ConversationStorageError('已有移动回执但当前归属未能确认，请保留原编号核对')
            if 'sra_conversations' not in known or not conn.execute('SELECT 1 FROM sra_conversations WHERE project_id=? AND conversation_id=?',(source,cid)).fetchone():
                raise ConversationStorageError('来源对话状态未能确认，不能声明本次未执行，请保留原编号核对')
            conn.execute(REFUSAL_SQL)
            conn.execute(f'INSERT OR IGNORE INTO {REFUSALS} VALUES (?,?,?,?,?,?,?)',
                         (cid,rid,token,target,404 if isinstance(exc,ConversationNotFound) else 409 if isinstance(exc,RevisionConflict) else 422,str(exc),now()))
            return self._refusal(conn,cid,rid,token)

    def confirm(self,source,target,cid,rid,token,*,fault=None):
        if source==target:raise WorkspaceError('当前项目不能作为移动目标')
        self.require_project(source);self.require_project(target)
        # Explicit confirmation may recover a hot rollback journal; GET never does.
        try:
            with closing(self._connect(source,target)) as recovery:
                recovery.execute('SELECT COUNT(*) FROM main.sqlite_master').fetchone()
                recovery.execute('SELECT COUNT(*) FROM destination.sqlite_master').fetchone()
        except sqlite3.Error as exc:raise ConversationStorageError('移动事务恢复尚未完成，请保留原编号稍后核对') from exc
        try:
            old=self.status(source,cid,rid)
            with closing(sqlite3.connect(self.paths.project_db(source))) as c:
                saved=json.loads(c.execute(f'SELECT data FROM {TABLE} WHERE move_id=?',(old['move_id'],)).fetchone()[0])
            if saved['target_project_id']!=target:raise RevisionConflict('移动 request_id 已用于不同目标')
            return old
        except ConversationNotFound:pass
        with closing(sqlite3.connect(self.paths.project_db(source).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            rejected=self._refusal(conn,cid,rid,token)
        if rejected:raise MoveRefused(rejected)
        try:
            with ExitStack() as locks:
                for pid in sorted((source,target)):
                    if not locks.enter_context(conversation_lock.hold(self.paths.project_db(pid),cid)):
                        raise RevisionConflict('此对话仍有操作执行中，移动未开始')
                with closing(self._connect(source,target)) as conn,conn:
                    conn.execute('BEGIN IMMEDIATE')
                    self.check_owner(source);self.check_owner(target)
                    # Duplicate confirmation may have waited for another commit.
                    if TABLE in tables(conn) and conn.execute(f'SELECT 1 FROM {TABLE} WHERE source_project_id=? AND conversation_id=? AND request_id=?',(source,cid,rid)).fetchone():
                        return self.status(source,cid,rid)
                    result,records,projects=self._inspect(conn,source,target,cid)
                    # Our own source lock is expected here; remove only this reason.
                    blocked=[r for r in result['reasons'] if r['code']!='working']
                    if blocked:raise WorkspaceError('不能移动：'+'；'.join(r['message'] for r in blocked))
                    if token!=result['token']:raise RevisionConflict('预检查后消息、材料或设置已变化，请重新核对再确认移动')
                    # Existing store initializer, within THIS attached transaction only.
                    for sql in DDL:conn.execute(sql.replace('CREATE TABLE IF NOT EXISTS ','CREATE TABLE IF NOT EXISTS destination.').replace('CREATE INDEX IF NOT EXISTS ','CREATE INDEX IF NOT EXISTS destination.').replace('CREATE UNIQUE INDEX IF NOT EXISTS ','CREATE UNIQUE INDEX IF NOT EXISTS destination.'))
                    # A target with unconverted legacy chat cannot be initialized by a move.
                    workspace=dict(projects[1]).get(KEY)
                    if workspace and json.loads(workspace).get('messages') and not conn.execute("SELECT 1 FROM destination.sra_chat_meta WHERE key='migration_version'").fetchone():
                        raise WorkspaceError('目标存在尚未转换的旧历史对话，请先在原界面核对')
                    conn.execute("INSERT OR IGNORE INTO destination.sra_chat_meta VALUES ('migration_version','1')")
                    meta=result['conversation'];columns=list(meta)
                    conn.execute('INSERT INTO destination.sra_conversations ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[target if k=='project_id' else meta[k] for k in columns])
                    if fault:fault('conversation_copied')
                    for table in ('sra_chat_requests','sra_chat_messages','sra_chat_failures','sra_call_audit'):
                        rows=records.get(table,[])
                        if not rows:continue
                        if table=='sra_call_audit':
                            create=conn.execute("SELECT sql FROM sqlite_master WHERE name='sra_call_audit'").fetchone()[0]
                            conn.execute(re.sub(r'^CREATE TABLE(?: IF NOT EXISTS)?\s+["\w]+','CREATE TABLE IF NOT EXISTS destination.sra_call_audit',create,flags=re.I))
                        for row in sorted(rows,key=lambda r:r.get('seq',0)):
                            values={k:(target if k=='project_id' else v) for k,v in row.items() if k!='seq'}
                            conn.execute(f'INSERT INTO destination.{table} ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
                    for key in ('discussion_step:',''):
                        if not key:continue
                        step=conn.execute('SELECT value FROM sra_chat_meta WHERE key=?',(key+cid,)).fetchone()
                        if step:conn.execute('INSERT INTO destination.sra_chat_meta VALUES (?,?)',(key+cid,step[0]))
                    previous=history(conn,cid);generation=max((h['generation'] for h in previous),default=0)+1
                    origins={}
                    for h in previous:origins.update(h['message_origins'])
                    for m in records['sra_chat_messages']:origins.setdefault(m['message_id'],source)
                    history_requests={}
                    for h in previous:history_requests.update(h['request_origins'])
                    for r in records.get('sra_chat_requests',[]):history_requests.setdefault(r['request_id'],source)
                    source_kv=dict(projects[0]);target_kv=dict(projects[1])
                    record={'move_id':uuid4().hex,'request_id':rid,'conversation_id':cid,'source_project_id':source,'target_project_id':target,'generation':generation,
                            'created_at':now(),'source_name':json.loads(source_kv.get('project.display_name',source_kv.get('research.topic','"原项目"'))),
                            'target_name':json.loads(target_kv.get('project.display_name',target_kv.get('research.topic','"目标项目"'))),
                            'create_request_id':meta['create_request_id'],'create_hash':meta['create_hash'],
                            'source_brief':json.loads(source_kv.get(KEY,'{}')).get('brief',{}),'message_origins':origins,'request_origins':history_requests,
                            'original_sequences':{m['message_id']:m['seq'] for m in records['sra_chat_messages']}}
                    conn.execute(SQL);conn.execute(SQL.replace('EXISTS '+TABLE,'EXISTS destination.'+TABLE))
                    for h in [*previous,record]:
                        args=(h['move_id'],h['request_id'],cid,h['source_project_id'],h['target_project_id'],h['generation'],encode(h))
                        for schema in ('main','destination'):
                            existing=conn.execute(f'SELECT data FROM {schema}.{TABLE} WHERE move_id=?',(h['move_id'],)).fetchone()
                            if existing and existing[0]!=encode(h):raise RevisionConflict('移动记录身份冲突，事务已取消')
                            conn.execute(f'INSERT OR IGNORE INTO {schema}.{TABLE} VALUES (?,?,?,?,?,?,?)',args)
                    if fault:fault('records_copied')
                    for table in ('sra_chat_messages','sra_chat_requests','sra_chat_failures','sra_call_audit','sra_chat_suggestions'):
                        if table in tables(conn):conn.execute(f'DELETE FROM {table} WHERE conversation_id=?',(cid,))
                    conn.execute('DELETE FROM sra_chat_meta WHERE key=?',('discussion_step:'+cid,))
                    conn.execute('DELETE FROM sra_conversations WHERE conversation_id=?',(cid,))
                    if fault:fault('source_removed')
            return self.status(source,cid,rid)
        except WorkspaceError:raise
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError) as exc:raise ConversationStorageError('移动事务未完成；原数据将按 SQLite 事务恢复，请保留原移动编号核对') from exc


def recover_journals(paths):
    """Explicit instance startup only, under its data lease. No DDL or task replay."""
    recovered=0
    for db in paths.projects.glob('*/knowledge.db'):
        journal=db.with_name(db.name+'-journal')
        if not journal.is_file() or journal.stat().st_size==0:continue
        from core.instance_state import is_link
        if is_link(db.parent) or is_link(db) or is_link(journal) or db.parent.resolve().parent!=paths.projects.resolve():
            raise ConversationStorageError('待恢复的 SQLite 日志路径不安全，未继续启动')
        with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=rw',uri=True)) as conn:
            conn.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ConversationStorageError('SQLite 恢复后的完整性检查失败')
        recovered+=1
    return recovered
