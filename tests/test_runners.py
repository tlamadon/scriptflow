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

# we create a fixture that replaces the subprocess call


# custom class to be the mock return value
# will override the requests.Response returned from requests.get

@pytest.mark.asyncio
async def test_runner(monkeypatch):

    def subprocess_return(arg):
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

    # job still queued (status Q) -> not completed yet
    monkeypatch.setattr(subprocess, "getstatusoutput",
        Mock(return_value=(0, "header1\nheader2\njob-id-1 c1 c2 c3 Q\n")))
    runner.update(controller)
    controller.add_completed.assert_not_called()

    # job completed (status C) -> reported back to the controller
    monkeypatch.setattr(subprocess, "getstatusoutput",
        Mock(return_value=(0, "header1\nheader2\njob-id-1 c1 c2 c3 C\n")))
    runner.update(controller)
    controller.add_completed.assert_called()


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


def test_submission_failure_marks_task_failed():
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
    assert t.fut.done()


def test_add_completed_failed_flag():
    # A non-zero exit (failed=True) is counted as a failure even when the output exists.
    controller = sf.core.Controller(runner=Mock(**{'available_slots.return_value': 1}))
    t = sf.Task(cmd="x", outputs=__file__, controller=controller)  # output exists
    t.schedule()
    controller.add_completed(t, failed=True)
    assert controller.failed == 1
    assert controller.done == 0
    assert t.fut.done()


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

    def fake_check_output(cmd):
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

    def fake_check_output(cmd):
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
