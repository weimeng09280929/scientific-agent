"""Normal local app with a budget ONLY for an explicitly authorized experience.

No fixtures, alternative business routes, model replacement or data-root changes.
Other projects retain normal runtime behavior. Never run without --allow-live.
"""
import argparse
import os
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--experience-id', required=True)
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('Current explicit live-call authorization is required')
    os.environ['SRA_WEB_PORT'] = str(args.port)
    from web.api import app, _CONFIG
    from core.invocation_audit import AuditScope
    import uvicorn

    pid = 'proj_' + uuid5(NAMESPACE_URL, 'sra:start:' + args.experience_id).hex
    prefix = '/api/projects/' + pid + '/'
    group = 'guided-experience:' + args.experience_id

    @app.middleware('http')
    async def bounded_experience(request, call_next):
        db = _CONFIG.paths.project_db(pid)
        if not request.url.path.startswith(prefix) or not db.is_file():
            return await call_next(request)
        scope = AuditScope(db, pid, group, group_id=group,
                           limits={'model':16,'source':6},
                           expected_model='deepseek-v4.1-flash-1017')
        with scope.activate():
            return await call_next(request)

    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1)


if __name__ == '__main__':
    main()
