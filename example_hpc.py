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

from time import sleep

SERVER_NAME = "acropolis"
USER_NAME = "lamadon"

@click.group()
def cli():
    pass

async def main():
    cr = sf.HpcRunner(3)    
    cr.log("starting loop")
    loop = asyncio.create_task(cr.loop())

    os.makedirs('.sf', exist_ok=True)

    tasks = []
    for i in range(5):
        tasks.append( 
            cr.createTask( 
                sf.Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
                    .result(f"res{i}.txt")
                    .uid(f"solve-{i}")
                        )
                    )

    i=10
    tasks.append( 
        cr.createTask( 
            sf.Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
                .result(f"res{-1}.txt")
                .uid(f"solve-{i}")
                .retry(2)
                    )
                )

    await asyncio.gather(*tasks)

@cli.command()
def all():
    asyncio.run(main())

if __name__ == '__main__':
    cli()