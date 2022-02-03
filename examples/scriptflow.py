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

import scriptflowlib as sf

from time import sleep

SERVER_NAME = "acropolis"
USER_NAME = "lamadon"

@click.group()
def cli():
    pass

async def main():
    cr = sf.HpcRunner(15)    
    cr.log("starting loop")
    loop = asyncio.create_task(cr.loop())

    conf = toml.load("jmp.toml")
    os.makedirs('.sf', exist_ok=True)

    i = 1
    tasks = []

    param_to_grid = conf['grid']['grid_var']

    for v in np.linspace(conf['grid']['beta'][0],conf['grid']['beta'][1],conf['grid']['n']).tolist():
        for v2 in np.linspace(conf['grid'][param_to_grid][0],conf['grid'][param_to_grid][1],conf['grid']['n']).tolist():
            conf['params']['beta'] = v
            conf['params'][param_to_grid] = v2

            with open("./.sf/input{}.toml".format(i), "w") as toml_file:
                toml.dump(conf, toml_file)
            
            tasks.append( 
                cr.createTask( 
                    sf.Task(f"julia main_parallel.jl --nproc 10 -c .sf/input{i}.toml -r ../results/last/res{i}.json -o ../results/last/output{i}.jld".split(" "))
                        .result(f"res{i}.json")
                        .input(f"input{i}.toml")
                        .uid(f"solve-{i}")
                            )
                        )
            i = i+1

    await asyncio.gather(*tasks)

    # we finally merge all the results
    data_all = {}
    files = glob.glob('../results/last/res[0-9]*.json')
    for fp in files:
        with open(fp) as f:
            data_all[os.path.basename(fp)] = json.load(f)

    with open('../results/last/results_all.json', 'w') as f:
        json.dump(data_all, f)

async def run_client():

    async with asyncssh.connect(SERVER_NAME, username=USER_NAME,
        options = asyncssh.SSHClientConnectionOptions(agent_path=os.environ["SSH_AUTH_SOCK"])) as conn:

        for fn in ['scriptflow.py','Manifest.toml','jmp.toml','scriptflow.py', 'scriptflowlib.py', 'last_sync.txt','hpc_script.sh','Project.toml','src','main_parallel.jl']:
            print("sending {}".format(fn))
            await asyncssh.scp( fn , (conn, '/home/{}/shared/jmp/code/'.format(USER_NAME)) , recurse=True)        
        
        result = await conn.run('qstat', check=True)

        print(result.stdout, end='')

@cli.command()
def deploy():
    asyncio.run(run_client())

@cli.command()
def all():
    asyncio.run(main())

if __name__ == '__main__':
    cli()