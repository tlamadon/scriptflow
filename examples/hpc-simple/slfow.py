#!/usr/bin/python3

# simple script flow

import os
import time
import toml
import asyncio
import json
import click

import asyncio, asyncssh, sys
import glob

import scriptflow.core as sf

from time import sleep

SERVER_NAME = "acropolis"
USER_NAME = "lamadon"

# set main maestro
cr = sf.HpcRunner(3)
sf.set_main_maestro(cr)

@click.group()
def cli():
    pass

async def main():
    asyncio.create_task(sf.get_main_maestro().loop())
    os.makedirs('.sf', exist_ok=True)

    tasks = [
        sf.Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
            .output(f"res{i}.txt").result(f"res{i}.txt")
            .uid(f"solve-{i}")                    
        for i in range(5) ]

    i=10
    tasks.append( 
        sf.Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
            .result(f"res{-1}.txt")
            .uid(f"solve-{i}")
            .retry(2))

    await asyncio.gather(*tasks)

@cli.command()
def all():
    asyncio.run(main())

if __name__ == '__main__':
    cli()