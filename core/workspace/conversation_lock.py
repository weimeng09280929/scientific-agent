"""OS-held per-conversation locks. No SQLite transaction spans a model call.

The kernel releases locks on process death. TTL/PID guesses cannot steal a
still-working process's request. Reads probe an existing file without creating it.
"""

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def lock_path(db: Path, conversation_id: str) -> Path:
    key = sha256((str(db.resolve()) + ":" + conversation_id).encode()).hexdigest()
    # Keep below Windows MAX_PATH even in pytest's deeply nested fixture roots.
    return db.parent / (".chat-" + key[:24] + ".lock")


def _lock(stream):
    stream.seek(0)
    if os.name == "nt":
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream):
    stream.seek(0)
    if os.name == "nt":
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def hold(db: Path, conversation_id: str):
    path = lock_path(db, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        acquired = False
        try:
            try:
                _lock(stream)
                acquired = True
            except (OSError, BlockingIOError):
                pass
            yield acquired
        finally:
            if acquired:
                _unlock(stream)


def is_held(db: Path, conversation_id: str) -> bool:
    path = lock_path(db, conversation_id)
    try:
        stream = path.open("r+b")
    except FileNotFoundError:
        return False
    except OSError:
        return True  # Unknown ownership must never authorize stealing a request.
    with stream:
        try:
            _lock(stream)
        except (OSError, BlockingIOError):
            return True
        _unlock(stream)
        return False
