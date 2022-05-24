"""
    Implementing tests using pytest and fixtures

"""

import pytest
from unittest.mock import MagicMock, Mock
import scriptflow as sf
import asyncio
import pytest_asyncio

@pytest_asyncio.fixture
async def controller(event_loop):

    runner = Mock(**{
        'available_slots.return_value' : 1})

    controller:sf.code.Controller = sf.core.Controller(runner=runner)
    controller.loop = event_loop.create_task(controller.update_loop(), name="controller")
    yield controller 
    await controller.shutdown()   

@pytest.mark.asyncio
async def test_example(controller):
    """No marker!"""

    t1 = sf.Task(cmd="test", controller=controller)
    t2 = sf.Task(cmd="test", controller=controller)
    controller.add(t1)
    controller.add(t2)

    assert t1.fut.done()==False
    assert t2.fut.done()==False

    controller.complete_task(t1)
    assert t1.fut.done()==True
    assert t2.fut.done()==False

    controller.complete_task(t2)
    assert t2.fut.done()==True

    t3 = sf.Task(cmd="test", controller=controller)
    controller.add(t3)
    controller.complete_task(t3)
    assert t3.fut.done()==True

    controller.add(t3)
    with pytest.raises(Exception):
        controller.complete_task(t3)
