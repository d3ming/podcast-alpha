"""Locked read-modify-write of a run's manifest.json: every writer goes through update() so none loses another's edits."""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path


def update(path, fn, default=None) -> dict:
    """flock a sidecar lock, read the current JSON (or `default` if missing), apply fn(manifest) in place, write atomically."""
    path = Path(path)
    with open(path.with_name(path.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            manifest = json.loads(path.read_text())
        elif default is not None:
            manifest = default() if callable(default) else copy.deepcopy(default)
        else:
            raise FileNotFoundError(path)
        fn(manifest)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        tmp.replace(path)
        return manifest
