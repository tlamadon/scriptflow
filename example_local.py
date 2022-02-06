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

@click.group()
def cli():
    pass

async def main():
    cr = sf.CommandRunner(3)
    cr.log("starting loop")
    loop = asyncio.create_task(cr.loop())

    os.makedirs('.sf', exist_ok=True)

    tasks = []
    for i in range(5):
        tasks.append( 
            cr.createTask( 
                sf.Task(["python", "-c", "import time; time.sleep(2); open('test_{}.txt','w').write('hello');".format(i)])
                    .result(f"res_{i}.txt")
                    .uid(f"solve-{i}")
                        )
                    )

    await asyncio.gather(*tasks)
    requests.get('http://scriptflow.lamadon.com/test.php?hash=12345&running=10')

@cli.command()
def all():
    asyncio.run(main())

if __name__ == '__main__':
    cli()
