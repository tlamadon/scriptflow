#!/usr/bin/python3

# simple script flow

import os
import time
import toml
import asyncio
import json
import click
import numpy as np

import asyncio, asyncssh, sys
import glob

import scriptflow.core as sf
import requests

from time import sleep

SERVER_NAME = "acropolis"
USER_NAME = "lamadon"

# set main maestro
cr = sf.CommandRunner(3)
sf.set_main_maestro(cr)

@click.group()
def cli():
    pass

async def flow_sleepit():

    tasks = []
    for i in range(5):
        t = sf.Task(["python", "-c", "import time; time.sleep(2); open('test_{}.txt','w').write('hello');".format(i)])
        t = t.output(f"test_{i}.txt").uid(f"solve-{i}")
        tasks.append(t)

    await asyncio.gather(*tasks)

    i = 10
    t = sf.Task(["python", "-c", "import time; time.sleep(2); open('test_{}.txt','w').write('hello');".format(i)])
    t.output(f"test_{i}.txt").uid(f"solve-{i}").add_deps("test_1.txt")

    await t

    requests.get('http://scriptflow.lamadon.com/test.php?hash=12345&running=10')


"""
======================== SCRIPTFLOW INTERNALS ==========================
"""

"""
    Main
"""
async def main(func):

    asyncio.create_task(sf.get_main_maestro().loop())
    os.makedirs('.sf', exist_ok=True)

    await func()      
    # await asyncio.gather(cf_vdec_growth(), cf_vdec_level())
    # requests.get('http://scriptflow.lamadon.com/test.php?hash=12345&running=10')

@cli.command()
@click.argument('name')
def run(name=""):

    func_names = globals().keys()
    flows = [w.replace("flow_","") for w in func_names if w.startswith("flow_")]

    if name not in flows:
        print("Flow {} is not available, values ares: {}".format(name, ", ".join(flows)))
        return()

    func = globals()["flow_{}".format(name)]
    asyncio.run(main(func))

if __name__ == '__main__':
    cli()
