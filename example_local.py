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

import scriptflow.scriptflowlib as sf
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

async def main():

    asyncio.create_task(sf.get_main_maestro().loop())
    os.makedirs('.sf', exist_ok=True)

    tasks = []
    for i in range(5):
        t = sf.Task(["python", "-c", "import time; time.sleep(2); open('test_{}.txt','w').write('hello');".format(i)])
        t = t.result(f"res_{i}.txt").uid(f"solve-{i}")
        tasks.append(t)
            
    await asyncio.gather(*tasks)
    requests.get('http://scriptflow.lamadon.com/test.php?hash=12345&running=10')

@cli.command()
def all():
    asyncio.run(main())

if __name__ == '__main__':
    cli()
