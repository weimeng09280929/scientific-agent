"""Read-only publication validator; never reseals or silently changes releases."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.workspace.teaching_packages import ROOT, load_package
from core.workspace.service import WorkspaceError

if __name__ == "__main__":
    try:
        identities = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("Invalid manifest:", exc)
        raise SystemExit(1)
    valid = bool(identities)
    for identity in sorted(identities):
        try:
            case_id, version = identity.split("/")
            load_package(case_id, version)
            print(identity, "OK")
        except (WorkspaceError, ValueError) as exc:
            valid = False
            print(identity, str(exc))
    raise SystemExit(0 if valid else 1)
