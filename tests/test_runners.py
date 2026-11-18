"""
    Implementing tests using pytest and fixtures

"""

import omegaconf
import pytest
from unittest.mock import MagicMock, Mock
import scriptflow as sf
import asyncio
import pytest_asyncio
import subprocess
import time
from pathlib import Path

from scriptflow.runners import cancel_attempts, pbs_rows, settle

# we create a fixture that replaces the subprocess call


# custom class to be the mock return value
# will override the requests.Response returned from requests.get

@pytest.mark.asyncio
async def test_runner(monkeypatch):

    def subprocess_return(arg, **kwargs):
        print("argument= {}".format(arg))
        return Mock(**{
            'decode.return_value' :"job-id-1\n"
        })

    # apply the monkeypatch for subprocess.checkoutput.decode() to mock_get
    monkeypatch.setattr(
        subprocess, 
        "check_output", 
        Mock(side_effect = subprocess_return))

    runner = sf.HpcRunner({'maxsize':5, 'user':'testuser', 'modules':'', 'walltime':'00:01:00'})
    t1 = sf.Task(cmd="test")
    runner.add(t1)

    controller = Mock(**{
            'available_slots.return_value' : 1})

    print(runner.processes)

    listing = "Job Id: job-id-1\n    Job_Name = sf-x\n    job_state = {}\n"

    # job still queued (status Q) -> not completed yet
    monkeypatch.setattr(subprocess, "check_output",
        Mock(return_value=listing.format("Q").encode()))
    runner.update(controller)
    controller.add_completed.assert_not_called()

    # job completed (status C) without exit evidence -> still not reported
    monkeypatch.setattr(subprocess, "check_output",
        Mock(return_value=listing.format("C").encode()))
    runner.update(controller)
    controller.add_completed.assert_not_called()

    # exit status known -> reported back to the controller
    monkeypatch.setattr(subprocess, "check_output",
        Mock(return_value=(listing.format("C") + "    exit_status = 0\n").encode()))
    runner.update(controller)
    controller.add_completed.assert_called_once_with(t1, failed=False)


def test_format_setup():
    from scriptflow.runners import format_setup
    assert format_setup([], None, "") == ""
    assert format_setup(["a"], ["b", "c"]) == "a\nb\nc"
    assert format_setup("a", ["b"]) == "a\nb"


def test_exit_code_from_log(tmp_path):
    from scriptflow.runners import exit_code_from_log
    assert exit_code_from_log(str(tmp_path / "missing.out")) is None

    p = tmp_path / "job.out"
    p.write_text("...\nExit Code: 0\n")
    assert exit_code_from_log(str(p)) == 0

    p.write_text("...\nExit Code: 7\nsome trailing line\n")
    assert exit_code_from_log(str(p)) == 7

    p.write_text("no exit code line here")
    assert exit_code_from_log(str(p)) is None


@pytest.mark.asyncio
async def test_submission_failure_marks_task_failed():
    # A failed submission (runner.add returns False) must be surfaced and counted,
    # not silently dropped (which would hang the run).
    failing_runner = Mock(**{
        'available_slots.side_effect': [1, 0, 0],
        'add.return_value': False,
    })
    controller = sf.core.Controller(runner=failing_runner)
    t = sf.Task(cmd="test", controller=controller)
    t.schedule()  # enqueues the task

    controller.update()

    assert controller.failed == 1
    assert isinstance(t.fut.exception(), sf.core.TaskFailed)


@pytest.mark.asyncio
async def test_add_completed_failed_flag():
    # A non-zero exit (failed=True) is counted as a failure even when the output exists.
    controller = sf.core.Controller(runner=Mock(**{'available_slots.return_value': 1}))
    t = sf.Task(cmd="x", outputs=__file__, controller=controller)  # output exists
    t.schedule()
    controller.add_completed(t, failed=True)
    assert controller.failed == 1
    assert controller.done == 0
    assert isinstance(t.fut.exception(), sf.core.TaskFailed)


def test_local_runner_reports_nonzero_exit(monkeypatch):
    # CommandRunner should report failed=True when the process exits non-zero.
    runner = sf.CommandRunner({'maxsize': 2})
    fake_proc = Mock(**{'poll.return_value': 3})
    runner.processes['h'] = {'proc': fake_proc, 'task': sf.Task(cmd="x"),
                             'start_time': '0'}
    controller = Mock()
    runner.update(controller)
    controller.add_completed.assert_called_once()
    assert controller.add_completed.call_args.kwargs.get('failed') is True


def test_task_setup_api():
    t = sf.Task(cmd="x")
    assert t.get_setup() == []
    t.set_setup("conda activate env")
    assert t.get_setup() == ["conda activate env"]
    t.set_setup(["a", "b"])
    assert t.get_setup() == ["a", "b"]
    assert sf.Task(cmd="x", setup="conda activate env").get_setup() == ["conda activate env"]


def test_pbs_setup_injected(monkeypatch):
    captured = {}

    def fake_check_output(cmd, **kwargs):
        captured['cmd'] = cmd
        return Mock(**{'decode.return_value': "job-id-1\n"})

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    runner = sf.HpcRunner({
        'maxsize': 5, 'user': 'u', 'modules': 'R/4.5', 'walltime': '00:01:00',
        'setup': ['export BASE=1'],
    })
    runner.add(sf.Task(cmd="Rscript x.R").set_setup(["conda activate env"]))

    content = open(captured['cmd'][-1]).read()
    assert "export BASE=1" in content
    assert "conda activate env" in content
    # executor setup precedes task setup; both sit between `module load` and `cd`
    assert content.index("module load") < content.index("export BASE=1")
    assert content.index("export BASE=1") < content.index("conda activate env")
    assert content.index("conda activate env") < content.index("cd ")


def test_slurm_setup_injected(monkeypatch):
    captured = {}

    def fake_check_output(cmd, **kwargs):
        captured['cmd'] = cmd
        return Mock(**{'decode.return_value': "Submitted batch job 123\n"})

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    runner = sf.HpcRunner_slurm({
        'maxsize': 5, 'user': 'u', 'account': 'a', 'partition': 'p',
        'modules': 'R/4.5', 'walltime': '00:01:00',
    })
    runner.add(sf.Task(cmd="Rscript x.R", setup=["conda activate env"]))

    content = open(captured['cmd'][-1]).read()
    assert "conda activate env" in content
    assert content.index("module load") < content.index("conda activate env") < content.index("cd ")
    # clean-environment design is preserved
    assert "--export=NONE" in captured['cmd']


def attempt(tmp_path, **fields):
    return dict(dict(state="queued", backend="slurm", job_id="123", submission_token="sf-token",
                     log=str(tmp_path / "attempt.out"), error=None), **fields)


def test_exit_marker_settles_absent_job(tmp_path):
    for code, state in ((0, "completed"), (3, "failed")):
        a = attempt(tmp_path)
        Path(a["log"]).write_text(f"Exit Code: {code}\n")
        assert settle(a, [])
        assert (a["state"], a["exit_code"]) == (state, code)


def test_quick_job_with_lost_acknowledgement(tmp_path):
    a = attempt(tmp_path, state="unknown", job_id=None)
    Path(a["log"]).write_text("Exit Code: 0\n")
    assert settle(a, [])
    assert a["state"] == "completed"


def test_absent_job_fails_after_grace(tmp_path, monkeypatch):
    a = attempt(tmp_path)
    assert not settle(a, [], grace=300)
    assert a["state"] == "queued"
    monkeypatch.setattr(time, "time", lambda: a["absent_since"] + 301)
    assert settle(a, [], grace=300)
    assert a["state"] == "failed"
    assert "no scheduler record" in a["error"]


def test_reappearing_job_restarts_grace(tmp_path):
    a = attempt(tmp_path)
    assert not settle(a, [])
    assert not settle(a, [("123", "sf-token", "running")])
    assert a["started_at"]
    assert "absent_since" not in a
    assert a["state"] == "running"


def test_lost_acknowledgement_reconciles_by_name(tmp_path):
    a = attempt(tmp_path, state="unknown", job_id=None)
    assert not settle(a, [("7", "other", "running"), ("123", "sf-token", "queued")])
    assert (a["job_id"], a["state"]) == ("123", "queued")


def test_pbs_rows(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value=(
        b"Job Id: 1.server\n    Job_Name = sf-a\n    job_state = R\n\n"
        b"Job Id: 2.server\n    Job_Name = sf-b\n    job_state = C\n    exit_status = 2\n\n"
        b"Job Id: 3.server\n    Job_Name = sf-c\n    job_state = C\n")))
    assert pbs_rows() == [("1.server", "sf-a", "running"), ("2.server", "sf-b", "failed")]


def test_duplicate_scheduler_matches_remain_unresolved(tmp_path, monkeypatch):
    a = attempt(tmp_path, state="unknown", job_id=None)
    cancel = Mock()
    monkeypatch.setattr(subprocess, "check_output", cancel)
    rows = [("123", "sf-token", "running"), ("124", "sf-token", "running")]
    assert cancel_attempts("slurm", [a], rows) == [a]
    assert a["job_id"] is None
    assert a["state"] == "unknown"
    cancel.assert_not_called()


def test_task_walltime_and_partition_override_the_executor():
    slurm = sf.HpcRunner_slurm({
        'maxsize': 1, 'user': 'u', 'account': 'a', 'partition': 'standard',
        'modules': '', 'walltime': '01:00:00',
    })
    default = slurm.build_script(sf.Task(cmd="x"))
    assert "--partition=standard" in default and "--time=01:00:00" in default
    script = slurm.build_script(sf.Task(cmd="x").set_walltime("00:10:00").set_partition("highmem"))
    assert "--partition=highmem" in script and "--time=00:10:00" in script

    pbs = sf.HpcRunner({'maxsize': 1, 'user': 'u', 'modules': '', 'walltime': '01:00:00'})
    assert "walltime=00:10:00" in pbs.build_script(sf.Task(cmd="x").set_walltime("00:10:00"))


def test_record_carries_requested_resources():
    from scriptflow.records import RunRecord, read_record
    record = RunRecord("test", "resources")
    task = sf.Task(cmd="x", name="t").set_cpu(4).set_memory(8).set_walltime("1:00:00")
    record.task(task)
    record.save()
    record.close()
    saved = read_record(record.path)["tasks"][task.execution_id]
    assert (saved["cpus"], saved["memory"], saved["walltime"], saved["partition"]) == (4, 8.0, "1:00:00", None)
