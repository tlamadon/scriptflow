import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from scriptflow.cli import cli, main
from scriptflow.core import Controller, RunCancelled, TaskFailed
from scriptflow.records import read_record, request_cancel, RunRecord
from scriptflow.runners import HpcRunner_slurm
from scriptflow.task import Task


def slurm():
    return HpcRunner_slurm(dict(maxsize=4, user="test", account="account", partition="partition",
                               modules="", walltime="00:10:00"))


@pytest.mark.asyncio
async def test_lost_acknowledgement_reconciled_without_resubmission(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    controller.start_record("test", "lost-ack")
    task = Task(cmd="echo done", controller=controller).schedule()
    submissions = []

    def scheduler(argv, **kwargs):
        if argv[0] == "sbatch":
            snapshot = read_record(controller.record.path)
            attempt = snapshot["tasks"][task.execution_id]["attempts"][0]
            assert attempt["state"] == "submitting"
            assert attempt["job_id"] is None
            submissions.append(argv)
            raise subprocess.TimeoutExpired(argv, 10)
        token = task.attempts[-1]["submission_token"]
        if argv[0] == "squeue":
            return f"123|{token}|RUNNING\n".encode()
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "check_output", scheduler)
    try:
        controller.update()
        assert task.state == "unknown"
        runner.update(controller)
        assert task.attempts[0]["job_id"] == "123"
        assert task.state == "running"
        controller.update()
        assert len(submissions) == 1
        assert len(task.attempts) == 1
    finally:
        task.fut.cancel()
        controller.record.close()


@pytest.mark.asyncio
async def test_query_errors_and_absence_do_not_complete_task(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    task = Task(cmd="true", controller=controller).schedule()
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=b"Submitted batch job 123"))
    controller.update()
    monkeypatch.setattr(subprocess, "check_output", Mock(side_effect=OSError("unavailable")))
    runner.update(controller)
    assert task.state == "unknown"
    assert not task.fut.done()
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=b""))
    runner.update(controller)
    assert task.state == "queued"
    assert not task.fut.done()
    token = task.attempts[-1]["submission_token"]
    monkeypatch.setattr(subprocess, "check_output",
                        Mock(return_value=f"123|{token}|TIMEOUT\n".encode()))
    runner.update(controller)
    with pytest.raises(TaskFailed):
        await task


@pytest.mark.asyncio
async def test_finished_job_waits_for_its_outputs(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    task = Task(cmd="true", outputs="late.txt", controller=controller).schedule()
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=b"Submitted batch job 123"))
    controller.update()
    token = task.attempts[-1]["submission_token"]
    monkeypatch.setattr(subprocess, "check_output",
                        Mock(return_value=f"123|{token}|COMPLETED\n".encode()))
    runner.update(controller)
    assert task.attempts[-1]["state"] == "completed"
    assert not task.fut.done()
    assert runner.size() == 1
    Path("late.txt").touch()
    runner.update(controller)
    assert await task is task
    assert runner.size() == 0


@pytest.mark.asyncio
async def test_outputs_still_missing_after_wait_fail(monkeypatch):
    runner = slurm()
    runner.output_wait = 0
    controller = Controller(runner=runner)
    task = Task(cmd="true", outputs="never.txt", controller=controller).schedule()
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=b"Submitted batch job 123"))
    controller.update()
    token = task.attempts[-1]["submission_token"]
    monkeypatch.setattr(subprocess, "check_output",
                        Mock(return_value=f"123|{token}|COMPLETED\n".encode()))
    runner.update(controller)
    with pytest.raises(TaskFailed, match="never.txt"):
        await task


@pytest.mark.asyncio
async def test_one_listing_per_poll(monkeypatch):
    runner = slurm()
    runner.max_proc = 50
    controller = Controller(runner=runner)
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=b"Submitted batch job 1"))
    tasks = [Task(cmd="true", controller=controller).schedule() for _ in range(20)]
    for _ in tasks:
        controller.update()
    assert runner.size() == 20
    listing = Mock(return_value=b"")
    monkeypatch.setattr(subprocess, "check_output", listing)
    runner.update(controller)
    assert listing.call_count == 1
    for task in tasks:
        task.fut.cancel()


@pytest.mark.asyncio
async def test_listing_runs_off_the_event_loop(monkeypatch):
    runner = slurm()
    runner.fetch = lambda: time.sleep(0.3) or []
    runner.processes = {"a": {"job": Mock(), "attempt": dict(state="completed")}}
    loop = asyncio.create_task(runner.loop(Mock()))
    started = time.monotonic()
    await asyncio.sleep(0.05)
    assert time.monotonic() - started < 0.2
    loop.cancel()


@pytest.mark.asyncio
async def test_record_writes_are_coalesced(monkeypatch):
    controller = Controller(runner=Mock(**{"available_slots.return_value": 0}))
    controller.start_record("test", "coalesced")
    saves = Mock(side_effect=controller.record.save)
    monkeypatch.setattr(controller.record, "save", saves)
    tasks = [Task(cmd="true", controller=controller).schedule() for _ in range(200)]
    assert saves.call_count == 0
    controller.sync_record()
    controller.sync_record()
    assert saves.call_count == 1
    assert len(read_record(controller.record.path)["tasks"]) == 200
    for task in tasks:
        task.fut.cancel()
    controller.record.close()


@pytest.mark.asyncio
async def test_record_write_failure(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    controller.start_record("test", "unwritable")
    task = Task(cmd="true", controller=controller).schedule()
    monkeypatch.setattr(controller.record, "save", Mock(side_effect=OSError("quota")))
    controller.sync_record()
    submit = Mock(return_value=b"Submitted batch job 1")
    monkeypatch.setattr(subprocess, "check_output", submit)
    controller.update()
    assert submit.call_count == 0
    with pytest.raises(TaskFailed):
        await task
    controller.record.close()


@pytest.mark.asyncio
async def test_missing_second_output_fails():
    controller = Controller()
    first = Path("first")
    first.touch()
    task = Task(cmd="true", outputs=["first", "second"], controller=controller).schedule()
    controller.add_completed(task)
    with pytest.raises(TaskFailed, match="second"):
        await task


@pytest.mark.asyncio
async def test_cancellation_stops_local_jobs_and_queued_tasks():
    controller = Controller(conf={"executors": {"local": {"maxsize": 1}}})
    controller.start_record("cancel", "cancel")

    async def flow():
        await asyncio.gather(Task(cmd="sleep 60", controller=controller),
                             Task(cmd="touch forbidden", controller=controller))

    running = asyncio.create_task(main(flow, controller))
    try:
        for _ in range(100):
            if any(task.attempts for task in controller.tasks.values()):
                break
            await asyncio.sleep(0.01)
        request_cancel(controller.record.path)
        with pytest.raises(RunCancelled):
            await asyncio.wait_for(running, timeout=5)
        data = read_record(controller.record.path)
        assert data["state"] == "cancelled"
        assert not data["cleanup_pending"]
        assert not Path("forbidden").exists()
        assert all(task["state"] == "cancelled" for task in data["tasks"].values())
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_waits_for_scheduler_confirmation(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    task = Task(cmd="true", controller=controller).schedule()
    listings = []

    def scheduler(argv, **kwargs):
        if argv[0] == "sbatch":
            return b"Submitted batch job 123"
        if argv[0] == "scancel":
            return b""
        token = task.attempts[-1]["submission_token"]
        listings.append(argv)
        state = "RUNNING" if len(listings) < 3 else "CANCELLED"
        return f"123|{token}|{state}\n".encode()

    monkeypatch.setattr(subprocess, "check_output", scheduler)
    monkeypatch.setattr("scriptflow.core.CANCEL_POLL", 0)
    controller.start_record("test", "cancel-wait")
    beats = Mock(side_effect=controller.record.heartbeat)
    monkeypatch.setattr(controller.record, "heartbeat", beats)
    controller.heartbeat_at = 0
    controller.update()
    assert not await controller.stop()
    assert beats.call_count >= 1
    controller.record.close()
    assert task.attempts[-1]["state"] == "cancelled"
    assert runner.size() == 0


@pytest.mark.asyncio
async def test_scheduler_cleanup_remains_pending_until_confirmed(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    controller.start_record("test", "pending-cleanup")
    task = Task(cmd="true", controller=controller).schedule()
    token = None

    def scheduler(argv, **kwargs):
        if argv[0] == "sbatch":
            return b"Submitted batch job 123"
        if argv[0] == "squeue":
            return f"123|{token}|RUNNING\n".encode()
        if argv[0] == "scancel":
            return b""
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "check_output", scheduler)
    controller.update()
    token = task.attempts[-1]["submission_token"]
    monkeypatch.setattr("scriptflow.core.CANCEL_WAIT", 0)
    assert await controller.stop()
    controller.record.finish("cancelled", cleanup_pending=True)
    controller.record.close()
    assert task.attempts[-1]["state"] == "cancel_requested"
    monkeypatch.setattr(subprocess, "check_output",
                        Mock(return_value=f"123|{token}|CANCELLED\n".encode()))
    result = CliRunner().invoke(cli, ["cleanup", "pending-cleanup"])
    assert result.exit_code == 0, result.output
    data = read_record(controller.record.path)
    assert not data["cleanup_pending"]
    assert next(iter(data["tasks"].values()))["state"] == "cancelled"


@pytest.mark.asyncio
async def test_controller_exception_cleans_siblings():
    controller = Controller()
    controller.start_record("test", "siblings")

    async def flow():
        await asyncio.gather(Task(cmd="sleep 60", controller=controller),
                             Task(cmd="exit 5", controller=controller))

    with pytest.raises(TaskFailed):
        await asyncio.wait_for(main(flow, controller), timeout=5)
    data = read_record(controller.record.path)
    assert data["state"] == "failed"
    assert not data["cleanup_pending"]
    assert {t["state"] for t in data["tasks"].values()} == {"failed", "cancelled"}


def wait_record(path, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = read_record(path)
            if predicate(data):
                return data
        except FileNotFoundError:
            pass
        time.sleep(0.02)
    raise AssertionError("Run record did not reach expected state")


def launch():
    Path("sflow.py").write_text("import scriptflow as sf\nsf.init({})\n"
                               "async def flow_test():\n    await sf.Task(cmd='sleep 60')\n")
    return subprocess.Popen([str(Path(sys.executable).with_name("scriptflow")),
                             "run", "test", "--run-id", "process"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_sigterm_persists_interruption():
    process = launch()
    path = Path(".sf/runs/process")
    try:
        wait_record(path, lambda d: any(t["state"] == "running" for t in d["tasks"].values()))
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=10)
        assert process.returncode != 0, stderr
        data = read_record(path)
        assert data["state"] == "interrupted"
        assert not data["cleanup_pending"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_dead_controller_preserves_job_identity():
    process = launch()
    path = Path(".sf/runs/process")
    attempt = None
    try:
        data = wait_record(path, lambda d: any(t["state"] == "running" for t in d["tasks"].values()))
        attempt = next(iter(data["tasks"].values()))["attempts"][0]
        process.kill()
        process.communicate(timeout=5)
        result = CliRunner().invoke(cli, ["cleanup", "process"])
        assert result.exit_code == 1
        data = read_record(path)
        assert data["state"] == "interrupted"
        assert data["cleanup_pending"]
        assert next(iter(data["tasks"].values()))["attempts"][0]["pid"] == attempt["pid"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if attempt:
            try:
                os.killpg(attempt["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_rejected_submission_fails_without_retry(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    task = Task(cmd="true", controller=controller).schedule()
    call = Mock(side_effect=subprocess.CalledProcessError(1, ["sbatch"], output=b"invalid account"))
    monkeypatch.setattr(subprocess, "check_output", call)
    controller.update()
    with pytest.raises(TaskFailed):
        await task
    assert runner.size() == 0
    assert call.call_count == 1


def test_cleanup_keeps_terminal_task_states(tmp_path, attempt):
    record = RunRecord("test", "states")
    done = attempt(state="completed")
    record.data["tasks"] = {
        "a": dict(state="completed", attempts=[done]),
        "b": dict(state="pending", attempts=[]),
    }
    record.finish("failed")
    record.close()
    result = CliRunner().invoke(cli, ["cleanup", "states"])
    assert result.exit_code == 0, result.output
    tasks = read_record(record.path)["tasks"]
    assert (tasks["a"]["state"], tasks["b"]["state"]) == ("completed", "cancelled")


@pytest.mark.asyncio
async def test_cancel_during_submission_preserves_accepted_job(monkeypatch):
    runner = slurm()
    controller = Controller(runner=runner)
    controller.start_record("test", "submit-cancel")
    submissions, names = [], []

    def scheduler(argv, **kwargs):
        if argv[0] == "sbatch":
            submissions.append(argv)
            names.append(argv[argv.index("--job-name") + 1])
            request_cancel(controller.record.path)
            return b"Submitted batch job 123"
        if argv[0] == "squeue":
            token = names[0]
            return f"123|{token}|RUNNING\n".encode()
        if argv[0] == "scancel":
            return b""
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "check_output", scheduler)
    monkeypatch.setattr("scriptflow.core.CANCEL_WAIT", 0)

    async def flow():
        await asyncio.gather(Task(cmd="first", controller=controller),
                             Task(cmd="second", controller=controller))

    with pytest.raises(RunCancelled):
        await asyncio.wait_for(main(flow, controller), timeout=3)
    data = read_record(controller.record.path)
    assert len(submissions) == 1
    assert data["cleanup_pending"]
    assert data["state"] == "cancelled"
    assert any(a["job_id"] == "123" for t in data["tasks"].values() for a in t["attempts"])


def test_two_cli_runs_have_independent_records_and_logs():
    Path("sflow.py").write_text("import scriptflow as sf\nsf.init({})\n"
                               "async def flow_test():\n    await sf.Task(cmd='echo concurrent')\n")
    processes = []
    try:
        for run_id in ("first-run", "second-run"):
            processes.append(subprocess.Popen([
                str(Path(sys.executable).with_name("scriptflow")), "run", "test", "--run-id", run_id,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        for process in processes:
            _, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
        paths = []
        for run_id in ("first-run", "second-run"):
            data = read_record(Path(".sf/runs") / run_id)
            assert data["state"] == "completed"
            attempt = next(iter(data["tasks"].values()))["attempts"][0]
            paths.append(attempt["log"])
            assert Path(attempt["log"]).read_text() == "concurrent\n"
        assert paths[0] != paths[1]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
