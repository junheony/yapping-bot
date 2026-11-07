"""The deployment patch matches the snapshot baseline and applies cleanly."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASHES = {
    "tg_post.py": "77d85ced8a7c6c83e481fd5503ed5425b49fe3b8e2c3652524bd5f46bed777b0",
    "tg_route.py": "b68473be1712bb01d03d27cf75d26752d66eca793e2946773348c38733ed7536",
    "tg_storyline.py": "bcaf2cc59e961d4c5a85b46cebfaf8de337f0ce4a016605338864376a6597c58",
    "tg_timing.py": "007e3f21fc13cf5dc323f236785d8d269f03b5d4a7f7722db0d0e3de0ec413b8",
}


def test_snapshot_hashes_match_transfer_metadata():
    for name, digest in HASHES.items():
        data = (ROOT / "deploy" / "snapshots" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest


def test_patch_applies_onto_snapshots(tmp_path):
    tree = tmp_path / "tree"
    (tree / "app" / "services").mkdir(parents=True)
    for name in HASHES:
        shutil.copy(ROOT / "deploy" / "snapshots" / name, tree / "app" / "services" / name)
    patch = (ROOT / "deploy" / "snapshot-relative.patch").read_bytes()
    dry = subprocess.run(["patch", "-p1", "--dry-run"], input=patch, cwd=tree, capture_output=True)
    assert dry.returncode == 0, dry.stderr.decode()
    applied = subprocess.run(["patch", "-p1"], input=patch, cwd=tree, capture_output=True)
    assert applied.returncode == 0, applied.stderr.decode()
    for name in HASHES:
        assert (tree / "app" / "services" / name).read_bytes() == (ROOT / "deploy" / "patched" / name).read_bytes()
    for rel in ("app/quality/tg_news.py", "app/quality/x_own.py", "app/quality/msg_status.py", "app/routers/msg_status.py"):
        assert (tree / rel).read_bytes() == (ROOT / rel).read_bytes()
    assert "app/main.py" not in patch.decode()
