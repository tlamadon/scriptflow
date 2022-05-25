"""
    The Runner interface is as follows:
    The runner is in charge of syncing files input/output, meaning if it is running remotely, it needs to send the input and bring back the output.

     - available_slots() returns a positive number with available resources, returns 0 if at capacity
     - add(task): this starts the job right away, if we are at capacity we should throw an error, the controller should be checking capacity
     - loop: a loop that runs in the event-loop, when a task completes, it calls the controller `add_completed`.

"""
from abc import ABC, abstractmethod

import asyncio
import logging
import queue
import subprocess
from datetime import datetime
from omegaconf import OmegaConf
import os, tempfile
import time

class AbstractRunner(ABC):

    @abstractmethod
    def size():
        pass

    @abstractmethod
    def available_slots():
        pass

    @abstractmethod
    def add(self, task):
        pass


class CommandRunner(AbstractRunner):

    def __init__(self, conf):
        conf = OmegaConf.create(conf)
        self.max_proc = conf.get('maxsize',4)
        self.processes = {}

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    """
        Start tasks
    """
    def add(self, task):

        if task.quiet:
            subp = subprocess.Popen(task.get_command(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT)
        else:
            subp = subprocess.Popen(task.get_command())
                #stdout=subprocess.DEVNULL,
                #stderr=subprocess.STDOUT)

        self.processes[task.hash] = {
            "proc" : subp, 
            "task": task , 
            'start_time': datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }                

    """
        Continuously checks on the tasks
    """
    async def loop(self, controller):
        while True:
            self.update(controller)                
            await asyncio.sleep(0.1)

    def update(self,controller):
        to_remove = []
        for (k,p) in self.processes.items():
            poll_val = p["proc"].poll()
            if poll_val is not None:
                to_remove.append(k)

        for k in to_remove:
            task = self.processes[k]["task"]
            del self.processes[k]       
            controller.add_completed( task )


class HpcRunner(AbstractRunner):

    script_template = """
    #PBS -N {name}
    #PBS -j oe
    #PBS -V
    #PBS -l procs={procs},mem={mem}
    #PBS -l walltime=12:00:00

    module load julia/1.6.1
    cd {wd}

    {cmd}
    """

    def __init__(self, conf):
        conf = OmegaConf.create(conf)
        self.max_proc = conf.maxsize
        self.processes = {}
        self.job_params = {'procs':1, 'mem' : "16Gb", 'name':'psub'}

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    def add(self, task):

        # create the script
        script_content = self.script_template.format(
            name = "sf-{}".format(task.uid),
            mem = task.mem,
            procs = task.ncore,
            wd = os.getcwd(), 
            cmd = " ".join(task.get_command()) )

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_script_filename = tmp.name
        tmp.write(script_content.encode())
        tmp.close()

        command = ["qsub", tmp_script_filename]
        try:
            output = subprocess.check_output(command).decode()
            JOB_ID = output.replace("\n","")
            
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"S", 'start':time.time()}          
        except subprocess.CalledProcessError as e:
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"F", 'start':time.time()}          

    def update(self, controller):

        # checking job status
        output = subprocess.check_output("qstat").decode()
        lines = output.split("\n") 
        job_status = {}
        for l in lines[2:]:                    
            vals = l.split()
            if len(vals)<3:
                continue
            job_status[vals[0]] = {'status':vals[4]}

        to_remove = []
        for (k,p) in self.processes.items():

            if p["JOB_ID"] not in job_status.keys():

                # we expire the job if longer than 5 minutes
                if time.time() - p["start"] > 5*60:
                    job_status[p["JOB_ID"]] = {'status':'C'}
                else:
                    continue

            if job_status[p["JOB_ID"]]['status'] == 'C':
                to_remove.append(k)

        for k in to_remove:
            del self.processes[k]
            controller.add_completed( p["job"] )

    async def loop(self,controller):
        while True:
            self.update(controller)                 
            await asyncio.sleep(2)

EXECUTORS = {    
    "local" : CommandRunner,
    "hpc" : HpcRunner,
}
