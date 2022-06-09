import click
import asyncio

# loading the use file
import importlib.util
from inspect import getmembers, isfunction

import scriptflow.core as sf
import os
import logging

from .glob import get_main_controller

# loading the local sflow.py file
def load_local_file(filename):
    spec = importlib.util.spec_from_file_location("", os.getcwd() + "/" + filename)
    foo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(foo)
    return foo

def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass  # Task cancellation should not be logged as an error.
    except Exception:  # pylint: disable=broad-except
        logging.exception('Exception raised by task = %r', task)

"""
    Main
"""
async def main(func, controller:sf.Controller):

    task_controller = asyncio.create_task( controller.start_loops() )
    task_controller.add_done_callback(_handle_task_result)

    os.makedirs('.sf', exist_ok=True)

    await func()      
    
    # let the other guys finish
    await asyncio.sleep(1)
    controller.last_word()


@click.group()
def cli():
    pass

@cli.command()
@click.argument('name')
@click.option('-f', '--force', 'force')
def run(name="",force=None,load="sflow.py"):

    foo = load_local_file("sflow.py")

    func_names = {k:v for (k,v) in getmembers(foo, isfunction)}
    flows = [w.replace("flow_","") for w in func_names.keys() if w.startswith("flow_")]

    if name not in flows:
        print("Flow '{}' is not available, choose from: {}".format(name, ", ".join(flows)))
        return()

    func = func_names["flow_{}".format(name)]

    controller = get_main_controller()
    controller.set_force_string(force)
    
    asyncio.run(main(func, controller))

# if __name__ == '__main__':
#     cli()