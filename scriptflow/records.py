"""
    Persisted record of a run, in <runs-dir>/<run-id>/ (default .sf/runs):

     - run.json: snapshot of the run, replaced atomically on every write
     - logs/: one .out (and scheduler .sh) per attempt
     - controller.lock: held by the controller for as long as it lives
     - cancel.json: written by `scriptflow cancel`, read by the controller

    run.json holds the run (run_id, flow, working_directory, host, controller_pid,
    state, created_at, updated_at, heartbeat, finished_at, error, cleanup_pending)
    and `tasks`, keyed by task id. Each task has name, command, state, outputs,
    error, the requested resources (cpus, memory in GB per CPU, walltime,
    partition; null where the executor's default applies) and its `attempts`,
    one per submission: attempt_id, number, backend, state, job_id (or pid and
    process_identity for local), submission_token, submitted_at, started_at,
    finished_at, log, error, host.

    Run states: running, completed, failed, cancelled, interrupted.
    Task and attempt states add: pending, submitting, queued, unknown, skipped,
    cancel_requested.
"""

import fcntl
import json
import os
import re
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
STALE_AFTER = 30   # seconds without a heartbeat before a running record reads as stale


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    """Write `data` to `path` so that a reader sees the old or the new file, never a part."""
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".snapshot-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)

        # the rename is durable once the directory is synced
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_path(run_id, root=".sf/runs"):
    """Directory of a run; the id is restricted so it cannot leave `root`."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise ValueError("Invalid run ID")
    return Path(root).resolve() / run_id


def read_record(path):
    data = json.loads((Path(path) / "run.json").read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported run record version")
    return data


def inspect_record(path, stale_after=STALE_AFTER):
    """The record plus `observation`: heartbeat age, staleness, pending cancel request.

    A stale heartbeat means the controller may be gone, not that the run ended.
    """
    data = read_record(path)
    heartbeat = datetime.fromisoformat(data["heartbeat"]).timestamp()
    age = max(0, time.time() - heartbeat)
    data["observation"] = {
        "heartbeat_age_seconds": age,
        "stale": data["state"] not in TERMINAL and age > stale_after,
        "cancel_requested": (Path(path) / "cancel.json").exists(),
    }
    return data


def request_cancel(path):
    """Leave a cancel request for the controller of a run that has not ended."""
    data = read_record(path)
    if data["state"] not in TERMINAL:
        atomic_json(Path(path) / "cancel.json", {"requested_at": now()})
    return data


class RunRecord:

    def __init__(self, flow, run_id=None, root=".sf/runs"):
        self.path = run_path(run_id or uuid.uuid4().hex, root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # fails on an existing run: an identity is never launched twice
        self.path.mkdir()
        (self.path / "logs").mkdir()

        self.lock = (self.path / "controller.lock").open("a+")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        stamp = now()
        self.data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.path.name,
            "flow": flow,
            "working_directory": str(Path.cwd()),
            "host": socket.gethostname(),
            "controller_pid": os.getpid(),
            "state": "running",
            "created_at": stamp,
            "updated_at": stamp,
            "heartbeat": stamp,
            "finished_at": None,
            "error": None,
            "cleanup_pending": False,
            "tasks": {},
        }
        self.dirty = False
        self.save()

    def save(self):
        self.data["updated_at"] = now()
        atomic_json(self.path / "run.json", self.data)
        self.dirty = False

    def heartbeat(self):
        self.data["heartbeat"] = now()
        self.save()

    def task(self, task):
        """Mirror a task into the record; written at the next save."""
        self.data["tasks"][task.execution_id] = {
            "task_id": task.execution_id,
            "name": task.uid,
            "command": task.get_command_str(),
            "state": task.state,
            "outputs": [str(Path(p).resolve()) for p in task.get_outputs()],
            "error": task.props.get("error"),
            "cpus": int(task.ncore),
            "memory": float(task.mem),
            "walltime": task.walltime,
            "partition": task.partition,
            "attempts": task.attempts,
        }
        self.dirty = True

    def finish(self, state, error=None, cleanup_pending=False):
        self.data.update(
            state=state,
            error=error,
            finished_at=now(),
            cleanup_pending=cleanup_pending)
        self.heartbeat()

    def close(self):
        self.lock.close()
