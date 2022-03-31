# simple script flow

import subprocess
import os
import time
#import toml
import asyncio
import tempfile

from rich.console import Console
from time import sleep
from os.path import exists

from dask_jobqueue import PBSCluster
from dask.distributed import Client


console = Console()


def dask_runner_exec(id, cmd):

    return("hello")    
    # subprocess.Popen(cmd, shell=True).check_output()
    # return(id)


class DaskRunner:

    def __init__(self, max_proc):


        self.max_proc = max_proc
        self.port = "13245"
        self.queue = []
        self.processes = {}
        self.finished = {}
        self.console = Console()
        self.done = 0
        self.failed = 0
        self.job_params = {'procs':1, 'mem' : "16Gb", 'name':'psub'}
        pass

    def start(self):

        cluster = PBSCluster(
            cores=1, 
            memory='2 GB', 
            walltime="24:00:00", 
            processes=1,
            scheduler_options={"dashboard_address": ":12345"})

        cluster.scale(self.max_proc)

        # create client
        self.client = Client(cluster)      
        console.log(self.client.scheduler_info())
  

    def shutdown(self):
        self.client.shutdown()

    def add(self,j):

        # check if job should be skipped
        if os.path.exists(j.output_file):
            # console.log("checking {}".format(j.output_file))
            output_time = os.path.getmtime(j.output_file)  

            UP_TO_DATE = True
            for f in j.deps:
                if os.path.getmtime(f) > output_time:    
                    console.log("{} input {} more recent than output".format(j.uid, f))
                    UP_TO_DATE = False
                    break 
            
            if UP_TO_DATE:
                console.log(f"up to date, skipping [red]{j.uid}[/red]")
                j.fut.set_result(j)
                return()       

        # we submit the job to dask client
        dask_fut = self.client.submit(dask_runner_exec,j.uid, j.cmd)
        # we store the reference tp the job to release it when done
        self.processes[j.uid] = {"job": j, "status":"S", "dask_fut" : dask_fut}          

    def log(self,str):
        console.log(str)

    async def loop(self):
        with console.status("Running ...") as status:
            while True:
                
                to_remove = []
                to_retry  = []

                # checking job status
                for (job_id, p) in self.processes.items():

                    if p['dask_fut'].done(): 
                        console.log("job {} finished".format(p["job"].uid))   

                        # checking that output file exists
                        if not exists(p["job"].return_file):

                            if  p["job"].retry>0:
                                console.log("[red]output file missing for {}, retrying... [/red]".format(p["job"].uid))  
                                p["job"].retry = p["job"].retry -1
                                to_retry.append(p["job"])
                            else:
                                console.log("[red]output file missing for {}, failing! [/red]".format(p["job"].uid))  
                                self.finished[p["job"].uid] = True
                                self.failed = self.failed + 1
                                p["job"].fut.set_result(p["job"])

                        else:
                            self.finished[p["job"].uid] = True
                            self.done = self.done +1
                            p["job"].fut.set_result(p["job"])

                        to_remove.append(k)

                for k in to_remove:
                    del self.processes[k]                 
                    status.update(f"running [green]queued:{len(self.queue)}[/green] [yellow]running:{len(self.processes)}[/yellow] [purple]done:{self.done}[/purple] [purple]failed:{self.failed}[/purple] ...")

                for k in to_retry:
                    self.add(k)

                status.update(f"running... procs:{len(self.processes)}")
                await asyncio.sleep(2)
