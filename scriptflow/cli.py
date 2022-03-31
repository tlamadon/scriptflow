import click
import asyncio

# loading the use file
import importlib.util
from inspect import getmembers, isfunction

import scriptflow.core as sf
import os

spec = importlib.util.spec_from_file_location("", os.getcwd() + "/sflow.py")
foo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(foo)

"""
    Main
"""
async def main(func):

    asyncio.create_task( sf.get_main_maestro().loop() )
    os.makedirs('.sf', exist_ok=True)

    await func()      
    # await asyncio.gather(cf_vdec_growth(), cf_vdec_level())
    # requests.get('http://scriptflow.lamadon.com/test.php?hash=12345&running=10')

@click.group()
def cli():
    pass

@cli.command()
@click.argument('name')
def run(name=""):

    func_names = {k:v for (k,v) in getmembers(foo, isfunction)}
    flows = [w.replace("flow_","") for w in func_names.keys() if w.startswith("flow_")]

    if name not in flows:
        print("Flow '{}' is not available, choose from: {}".format(name, ", ".join(flows)))
        return()

    func = func_names["flow_{}".format(name)]
    asyncio.run(main(func))

# if __name__ == '__main__':
#     cli()