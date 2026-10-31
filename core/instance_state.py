"""Data-directory lifetime leases shared by the web process and offline tools."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import threading

from core.workspace.conversation_lock import _lock, _unlock

_mutex = threading.RLock()
_uses = {}
_unfinished = []  # Retain leases until process exit if work survived ASGI shutdown.
MARKER = ".sra-instance.json"


def is_link(path):
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    # st_file_attributes is available before pathlib.is_junction (Python 3.12).
    # Reject every Windows reparse point, including directory junctions.
    return Path(path).is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_directory(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if is_link(part):
            raise RuntimeError("数据路径含链接或目录联接，不能确认边界")
    return path.resolve()


@contextmanager
def data_lease(root, projects, account_db=None):
    """Nonblocking OS locks; deliberately no TTL or stale-lock deletion."""
    paths = {safe_directory(root) / ".sra-instance.lock",
             safe_directory(projects) / ".sra-data.lock"}
    if account_db is not None:
        db = Path(account_db)
        paths.add(safe_directory(db.parent) / (".sra-accounts-" + db.name + ".lock"))
    with ExitStack() as stack:
        for path in sorted(paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            if is_link(path) or (path.exists() and path.stat().st_nlink != 1):
                raise RuntimeError("实例锁路径不安全")
            stream = stack.enter_context(path.open("a+b"))
            if path.stat().st_size == 0:
                stream.write(b"0"); stream.flush()
            try:
                _lock(stream)
            except OSError:
                raise RuntimeError("该数据目录仍被实例或维护操作占用；请正常停止后再试") from None
            stack.callback(_unlock, stream)
        yield


@contextmanager
def running_instance(root, projects, account_db, mode, *, clean_shutdown=lambda: True):
    root, projects = safe_directory(root), safe_directory(projects)
    if (root / "INCOMPLETE").exists() or root.name.startswith((".sra-b-", ".sra-r-")):
        raise RuntimeError("该目录是未完成的恢复，不能作为实例启动")
    key = (str(root), str(projects))
    def state_record(state):
        return {"version": 1, "mode": mode, "pid": os.getpid(), "state": state,
                "projects": str(projects), "accounts_db": str(Path(account_db).resolve()) if account_db else None}
    # Multiple TestClients in one process represent one application, not workers.
    with _mutex:
        if key in _uses:
            _uses[key][1] += 1
        else:
            receipt = root / ".sra-restore.json"
            if receipt.is_file():
                if receipt.is_symlink(): raise RuntimeError("恢复配置路径不安全")
                restored = json.loads(receipt.read_text(encoding="utf-8"))
                spec = restored.get("layout", {})
                if restored.get("version") != 1 or spec.get("mode") != ("demo" if mode == "local" else mode):
                    raise RuntimeError("恢复目录的实例模式不匹配，不能切换为免登录访问")
            lease = data_lease(root, projects, account_db)
            lease.__enter__()
            try:
                marker = root / MARKER
                if is_link(marker) or (marker.exists() and marker.stat().st_nlink != 1):
                    raise RuntimeError("实例状态路径不安全")
                marker.write_text(json.dumps(state_record("running")), encoding="utf-8")
            except BaseException:
                lease.__exit__(None, None, None)
                raise
            _uses[key] = [lease, 1]
    try:
        yield
    finally:
        with _mutex:
            record = _uses[key]; record[1] -= 1
            if record[1] == 0:
                released = False
                try:
                    state = "stopped" if clean_shutdown() else "unclean"
                    (root / MARKER).write_text(json.dumps(state_record(state)), encoding="utf-8")
                    released = state == "stopped"
                finally:
                    if released:
                        record[0].__exit__(None, None, None)
                    else:
                        _unfinished.append(record[0])
                    del _uses[key]
