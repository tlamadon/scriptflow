from scriptflow.core import Controller
import pytest

from scriptflow.cli import main as sf_main

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from scriptflow.cli import cli
from scriptflow.records import RunRecord, read_record, inspect_record

async def simple_flow():
    await asyncio.sleep(0.1)

@pytest.mark.asyncio
async def test_main():

    controller = Controller()
    controller.start_record("test", "main")

    await sf_main(simple_flow, controller)
    assert read_record(controller.record.path)["state"] == "completed"


def write_flow(body):
    Path("sflow.py").write_text("import scriptflow as sf\nsf.init({'executors': {'local': {'maxsize': 4}}})\n"
                               + "async def flow_test():\n" + body)


def test_cli_success_and_offline_inspection():
    write_flow("    await sf.bag(sf.Task(cmd='echo first'), sf.Task(cmd='echo first'))\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "test", "--run-id", "success"])
    assert result.exit_code == 0, result.output
    Path("sflow.py").write_text("raise Exception('must not import')")
    result = runner.invoke(cli, ["inspect", "success", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["state"] == "completed"
    tasks = list(data["tasks"].values())
    assert len(tasks) == 2
    logs = [task["attempts"][0]["log"] for task in tasks]
    assert len(set(logs)) == 2
    assert all(Path(log).read_text() == "first\n" for log in logs)


def test_cli_failure_blocks_dependents():
    write_flow("    await sf.Task(cmd='exit 7')\n    await sf.Task(cmd='touch forbidden')\n")
    result = CliRunner().invoke(cli, ["run", "test", "--run-id", "failure"])
    assert result.exit_code == 1
    assert not Path("forbidden").exists()
    data = read_record(".sf/runs/failure")
    assert data["state"] == "failed"
    assert len(data["tasks"]) == 1


def test_unknown_flow_and_duplicate_identity_fail():
    write_flow("    pass\n")
    runner = CliRunner()
    assert runner.invoke(cli, ["run", "missing"]).exit_code == 1
    assert runner.invoke(cli, ["run", "test", "--run-id", "once"]).exit_code == 0
    assert runner.invoke(cli, ["run", "test", "--run-id", "once"]).exit_code == 1
    assert runner.invoke(cli, ["run", "test", "--run-id", "../escape"]).exit_code == 1


def test_existing_run_id_is_rejected_before_importing_the_flow():
    write_flow("    pass\n")
    runner = CliRunner()
    assert runner.invoke(cli, ["run", "test", "--run-id", "once"]).exit_code == 0
    Path("sflow.py").write_text("raise Exception('must not import')")
    result = runner.invoke(cli, ["run", "test", "--run-id", "once"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_retry_logs_and_attempts():
    write_flow("    await sf.Task(cmd='if [ -f flag ]; then echo ok; else touch flag; exit 1; fi').set_retry(1)\n")
    result = CliRunner().invoke(cli, ["run", "test", "--run-id", "retry"])
    assert result.exit_code == 0, result.output
    task = next(iter(read_record(".sf/runs/retry")["tasks"].values()))
    assert [a["state"] for a in task["attempts"]] == ["failed", "completed"]
    assert len({a["log"] for a in task["attempts"]}) == 2


def test_cancel_is_idempotent_and_cleanup_refuses_active_controller():
    record = RunRecord("test", "active")
    try:
        runner = CliRunner()
        assert runner.invoke(cli, ["cancel", "active"]).exit_code == 0
        assert runner.invoke(cli, ["cancel", "active"]).exit_code == 0
        result = runner.invoke(cli, ["cleanup", "active"])
        assert result.exit_code == 1
        assert "controller is active" in result.output
        assert inspect_record(record.path)["observation"]["cancel_requested"]
    finally:
        record.close()
    assert runner.invoke(cli, ["cleanup", "active"]).exit_code == 0
    assert read_record(record.path)["state"] == "interrupted"


def test_stale_heartbeat_is_not_a_terminal_outcome():
    record = RunRecord("test", "stale")
    record.data["heartbeat"] = "2000-01-01T00:00:00+00:00"
    record.save()
    record.close()
    data = inspect_record(record.path)
    assert data["state"] == "running"
    assert data["observation"]["stale"]
