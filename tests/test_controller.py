from unittest.mock import MagicMock, Mock

import scriptflow as sf
from omegaconf import OmegaConf
from scriptflow.core import Controller
import pytest

import asyncio


def test_controller_init(event_loop):

    task1 = Mock(**{
        'output_file' :"tmp.txt",
        'props' : {},
        'deps':[],
        'fut': event_loop.create_future(),
        'hash': "somehash",
        'get_command.return_value' :  "bla bla",
        'get_outputs.return_value' :  []})

    task2 = Mock(**{
        'output_file' :"tmp.txt",
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

