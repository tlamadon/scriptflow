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

    sub_process = Mock(**{
        'decode.return_value' :"job-id-1\n"
        })

    # apply the monkeypatch for subprocess.checkoutput.decode() to mock_get
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value = sub_process))

    runner = sf.HpcRunner({'maxsize':5})
    t1 = sf.Task(cmd="test")
    runner.add(t1)

    controller = Mock(**{
            'available_slots.return_value' : 1})

    print(runner.processes)

    sub_process = Mock(**{
        'decode.return_value' :"""\n\njob-id-1 c1 c2 c3 Q\n"""
        })
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value = sub_process))
    controller.add_completed.assert_not_called()

    sub_process = Mock(**{
        'decode.return_value' :"""\n\njob-id-1 c1 c2 c3 C\n"""
        })
    monkeypatch.setattr(subprocess, "check_output", Mock(return_value = sub_process))

    # call update on the runner
    runner.update(controller)
    controller.add_completed.assert_called()
