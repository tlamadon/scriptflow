from unittest.mock import Mock

import scriptflow as sf
from scriptflow.core import Controller
import pytest

import asyncio


@pytest.mark.asyncio
async def test_controller_init():
    event_loop = asyncio.get_running_loop()

    task1 = Mock(**{
        'outputs':{None:['tmp.txt']},
        'props' : {},
        'deps':[],
        'fut': event_loop.create_future(),
        'hash': "somehash",
        'get_command.return_value' :  "bla bla",
        'get_outputs.return_value' :  []})

    task2 = Mock(**{
        'outputs':{None:['tmp.txt']},
        'props' : {},
        'deps':[],
        'fut': event_loop.create_future(),
        'hash': "somehash2",
        'get_command.return_value' :  "bla bla",
        'get_outputs.return_value' :  []})


    runner = Mock(**{
        'available_slots.return_value' : 1})

    controller = sf.core.Controller(runner=runner)

    controller.add(task1)
    controller.add(task2)
    task1.assert_not_called()

    # next we call update on the controller
    # the controller should check inputs/ouputs
    controller.update()
    controller.update()

    # at this point the runner should have assigned the task
    runner.add.assert_called()

    # we then call complete on the task
    controller.add_completed(task1)

    # check that task was completed
    task1.set_completed.assert_called()
    task2.set_completed.assert_not_called()

    controller.add_completed(task2)
    task2.set_completed.assert_called()

@pytest.mark.asyncio
async def test_controller_retry():
    event_loop = asyncio.get_running_loop()

    task1 = Mock(**{
        'outputs' : {None:["tmp.txt"]},
        'props' : {},
        'deps':[],
        'retry':1,
        'fut': event_loop.create_future(),
        'hash': "somehash",
        'get_command.return_value' :  "bla bla",
        'get_outputs.return_value' :  ['tmp.txt']})

    runner = Mock(**{
        'available_slots.return_value' : 1})

    controller = sf.core.Controller(runner=runner)

    controller.add(task1)
    task1.assert_not_called()
    controller.update()

    # at this point the runner should have assigned the task
    runner.add.assert_called()

    # we then call complete on the task
    controller.add_completed(task1)
    # here we should fail because output file is missing
    assert task1.retry == 0

    # the following should call the runner again
    controller.update()
    controller.add_completed(task1)

    # the retry is used up: the task fails
    task1.set_completed.assert_not_called()
    assert isinstance(task1.fut.exception(), sf.core.TaskFailed)
    assert controller.failed == 1


def test_all_outputs_and_oldest_output_checked(tmp_path):
    import os
    controller = Controller()
    first, second, source = (tmp_path / name for name in ("first", "second", "source"))
    first.touch()
    source.touch()
    task = sf.Task(cmd="true", outputs=[str(first), str(second)], inputs=str(source))
    assert not controller.check_task_uptodate(task)
    second.touch()
    os.utime(first, (300, 300))
    os.utime(second, (100, 100))
    os.utime(source, (200, 200))
    assert not controller.check_task_uptodate(task)
    os.utime(second, (300, 300))
    assert controller.check_task_uptodate(task)
