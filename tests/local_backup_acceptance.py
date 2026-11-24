"""Loopback/offline backup acceptance only, with explicit disposable roots."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from core.local_backup import inventory, layout, write_json


def disposable(path):
    path = path.resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if not path.is_relative_to(temp) or not any(p.name.startswith("sra-backup-") for p in (path, *path.parents)):
        raise ValueError("Only this batch's explicit sra-backup-* temporary directory is supported")
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("prepare", "serve", "finalize"))
    p.add_argument("--root", type=Path, required=True, help="Disposable base containing source/backup/restored")
    p.add_argument("--port", type=int, default=8094)
    p.add_argument("--instance", default="restored", help="New restored child directory; permits reruns without overwriting evidence")
    p.add_argument("--evidence", type=Path, default=Path("docs/local-backup-restore"))
    args = p.parse_args(); base = disposable(args.root)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.instance): raise ValueError("Instance child name is invalid")
    restored_root = base / args.instance
    args.evidence.mkdir(parents=True, exist_ok=True)
    if args.mode == "prepare":
        if base.exists() and any(base.iterdir()): raise ValueError("A new empty acceptance directory is required")
        base.mkdir(exist_ok=True)
        from tests.local_backup_fixture import seed
        sample = seed(base / "source")
        public = {k: v for k, v in sample.items() if not k.startswith("_")}
        write_json(base / "sample.json", public)
        commands = [
            ["precheck", "--root", str(base / "source"), "--mode", "accounts", "--confirm-stopped"],
            ["backup", "--root", str(base / "source"), "--mode", "accounts", "--confirm-stopped", "--destination", str(base / "backup")],
            ["verify", "--backup", str(base / "backup")],
            ["restore", "--backup", str(base / "backup"), "--destination", str(restored_root)],
        ]
        results = []
        for command in commands:
            r = subprocess.run([sys.executable, "-m", "tools.local_backup", *command], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode: raise RuntimeError("Offline backup command failed: " + r.stdout)
            results.append({"command": command, "result": json.loads(r.stdout)})
        _, spec = layout(base / "source", "accounts")
        write_json(base / "source-inventory.json", inventory(base / "source", spec))
        write_json(args.evidence / "preparation.json", {"base": str(base), "commands": results, "sample": public, "realExternalCalls": 0})
        print(json.dumps({"ok": True, "base": str(base), "realExternalCalls": 0}))
    elif args.mode == "serve":
        if args.port in (8001, 8002, 8077) or not (restored_root / ".sra-restore.json").is_file():
            raise ValueError("Only a restored disposable instance and nonreserved port are permitted")
        import uvicorn
        from tests.local_backup_fixture import app_at
        sample = json.loads((base / "sample.json").read_text(encoding="utf-8"))
        with app_at(restored_root) as (api, state):
            server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=args.port, log_level="warning"))
            @api.app.get("/__backup__/state")
            def status(): return {"guidance_calls": state["guidance_calls"], "model_calls": state["model_calls"], "source_calls": state["source_calls"], "public_calls": state["public_calls"], "sample": sample}
            @api.app.post("/__backup__/shutdown")
            def shutdown(): server.should_exit = True; return {"stopping": True}
            print("Restored offline acceptance on loopback port " + str(args.port), flush=True)
            server.run()
    else:
        _, spec = layout(base / "source", "accounts")
        before = json.loads((base / "source-inventory.json").read_text(encoding="utf-8"))
        assert inventory(base / "source", spec) == before, "Original business data changed"
        marker = json.loads((restored_root / ".sra-instance.json").read_text(encoding="utf-8"))
        assert marker["state"] == "stopped", "Restored instance has not stopped normally"
        write_json(args.evidence / "source-unchanged.json", {"passed": True, "sourceBusinessFilesUnchanged": len(before), "restoredInstanceStopped": True, "realExternalCalls": 0})
        print("PASS source unchanged and restored instance stopped")


if __name__ == "__main__": main()
