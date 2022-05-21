import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, Mock

import scriptflow as sf
from omegaconf import OmegaConf
from scriptflow.core import Controller

import asyncio

class TestStringMethods(unittest.TestCase):

    def test_upper(self):
        self.assertEqual('foo'.upper(), 'FOO')


class TestController(IsolatedAsyncioTestCase):

    def test_controller_init(self):


        task1 = Mock(**{
            'output_file' :"tmp.txt",
            'props' : {},
            'deps':[],
            'fut': asyncio.get_event_loop().create_future(),
            'hash': "somehash",
            'get_command.return_value' :  "bla bla",
            'get_outputs.return_value' :  []})

        task2 = Mock(**{
            'output_file' :"tmp.txt",
            'props' : {},
            'deps':[],
            'fut': asyncio.get_event_loop().create_future(),
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

        # at this point the runner should have assigned the task
        runner.add.assert_called_once()

        # we then call complete on the task
        controller.add_completed(task1)







        

if __name__ == '__main__':
    unittest.main()