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

    t4 = sf.Task(cmd="test", controller=controller)
    t4.schedule()
    assert t4.is_scheduled()

    t5 = sf.Task(cmd="test", controller=controller)
    t5.set_prop("test","test")
    assert t5.get_prop("test")=="test"

    t5.add_deps("dep1.txt")
    t5.add_deps(["dep2.txt","dep3,txt"])
    assert len(t5.deps)==3

    t5.output("output.txt")
    assert t5.get_outputs() == ["output.txt"]

    t5.uid("myuid")