# simple script flow

import subprocess
import os
import time
#import toml
import asyncio
import tempfile
from datetime import datetime
import asyncio
import logging
import queue
import requests

from rich.console import Console
from time import sleep
from os.path import exists
from tinydb import TinyDB, Query
from omegaconf import OmegaConf

console = Console()

# print(toml.load("jmp.toml"))

# with open('jmp.toml', 'w') as f:
#     new_toml_string = toml.dump(parsed_toml, f)


# processes = set(
#     "julia main_parallel.jl --nproc 10",
#     "julia main_parallel.jl --nproc 10"
# )

# ["python", "-c", "import time; time.sleep(3); print('done')"]

def bag(*args, **kwargs):
    return(asyncio.gather(*args, **kwargs))


from rich.console import Console
from time import sleep


"""
This class creates the link between the user and the different executors.
It also contains the main task logic that doesn't have to do with simply running the command, such 
as checking if the task needs to be run, and wether it has run correctly.
"""
class Controller:

    def __init__(self, conf) -> None:
        self.history = TinyDB('sf.json')
        self.task_queue = queue.Queue()
        self.done   = 0
        self.retry  = 0
        self.failed = 0
        self.last_notify = ""
        self.notifty_hash = conf.notify

        self.msg_queue = queue.Queue()

        # for now we use one executor
        if conf.executors:
            print(conf.executors)
            self.executors = { k:EXECUTORS[k](v) for k,v in conf.executors.items()}
        else:
            self.executors = { "local": CommandRunner(10) }

    """
    The user tells us that a task should be added
    """
    def add(self, task):

        # check if the task is up to date, 
        # this allows to skip the task right away!
        if self.check_task_uptodate(task):
            self.complete_task(task)
            return

        # otherwise, we add the task to the queue
        self.task_queue.put(task)

    """
    An executor reports a finished task
    """
    def add_completed(self, task):

        task.props["finish_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # retry if output file doesn't exist
        if (len(task.return_file)>0) & (not exists(task.return_file)):

            if  task.retry>0:
                self.log("[red]output file missing for {}, retrying... [/red]".format(task.uid))  
                task.retry = task.retry - 1
                self.add(task)
            else:
                self.log("[red]output file missing for {}, failing! [/red]".format(task.uid))  
                self.failed = self.failed + 1
                self.complete_task(task)

        # everything normal
        else:
            self.done = self.done +1
            self.complete_task(task)

    """
        Main loop that takes tasks from the global queue and send them to executors.
        This should not send tasks if the executor queue is already full.

        We might want to make this event-driven, ie we check only if:
        - a new task is added
        - a task finished
    """
    def update(self):

        if self.task_queue.empty():
            return

        # we check to see if any exectutor has space in their queues
        for exec in self.executors.values():
            if exec.available_slots()>0:
                # take the next task
                task = self.task_queue.get()

                # send it to the executor
                task.props["start_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                exec.add(task)

    """
    Check if the task is up to date 
    """
    def check_task_uptodate(self, task):

        # checking if the task needs to be redone
        if os.path.exists(task.output_file):
            # console.log("checking {}".format(j.output_file))
            output_time = os.path.getmtime(task.output_file)  

            UP_TO_DATE = True
            for f in task.deps:
                if os.path.getmtime(f) > output_time:    
                    self.log("task [red]{}[/red]: input [green]{}[/green] more recent than output".format(task.uid, f))
                    UP_TO_DATE = False
                    break 
            
            if UP_TO_DATE:
                self.log(f"task [red]{task.uid}[/red] is up to date, skipping it.")
                return                                                     

        self.log(f"adding [red]{task.uid}[/red]")
        self.log(" - cmd: {}".format( " ".join(task.get_command() ) ))

    def complete_task(self, task):
        # load the results if any
        # TBD

        self.update_history(task)

        # mark the promise as completed and set it to itself
        assert asyncio.isfuture(task.fut)
        assert not task.fut.done()
        task.fut.set_result(task)

    def update_history(self, task):
        # append job to history - replace this with upsert
        tj = Query()
        self.history.upsert({
            'deps' : task.deps,
            'output' : task.output_file,
            'cmd': task.get_command(),
            'start_time': task.props['start_time'],
            'end_time': task.props['finish_time']
        }, tj.hash == task.hash)


    async def start_loops(self):
        for exec in self.executors.values():
            asyncio.create_task(exec.loop(self))

        # run my own event loop
        with console.status("Running ...") as status:
            while True:
                while not self.msg_queue.empty():
                    msg = self.msg_queue.get()
                    console.log(msg)

                self.update()

                running = 0
                capacity = 0
                for exec in self.executors.values():
                    running += exec.size()
                    capacity += exec.size() + exec.available_slots()

                status.update(f"running [green]queued:{self.task_queue.qsize()} [/green] [yellow]running:{running}/{capacity} [/yellow] [purple]done:{self.done} / failed:{self.failed}[/purple] ...")
                # send updates to sever if any
                self.notify(running, capacity)

                await asyncio.sleep(0.1)

    def log(self, str):
        self.msg_queue.put(str)

    """
    notify server
    """
    def notify(self,running, capacity):

        notify_str = "running={}&queued={}&completed={}&failed={}&retry={}".format(
            running, self.task_queue.qsize(), self.done, self.failed, self.retry)

        if notify_str != self.last_notify:
            requests.get('http://scriptflow.lamadon.com/test.php?hash={}&{}'.format(self.notifty_hash, notify_str))
            self.last_notify = notify_str

    """
    called when flow finishes
    """
    def last_word(self):
        console.log("--------------------------------")
        console.log("Thank you for using scriptflow!")
        console.log("You ran {} tasks with {} fails.".format(self.done, self.failed))
        console.log("--------------------------------")


"""
    The Runner interface is as follows:
    The runner is in charge of syncing files input/output, meaning if it is running remotely, it needs to send the input and bring back the output.

     - available_slots() returns a positive number with available resources, returns 0 if at capacity
     - add(task): this starts the job right away, if we are at capacity we should throw an error, the controller should be checking capacity
     - loop: a loop that runs in the event-loop, when a task completes, it calls the conductor `add_completed`.

"""

class CommandRunner:

    def __init__(self, conf):
        self.max_proc = conf.maxsize
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

            to_remove = []
            for (k,p) in self.processes.items():
                poll_val = p["proc"].poll()
                if poll_val is not None:
                    to_remove.append(k)

            for k in to_remove:
                task = self.processes[k]["task"]
                del self.processes[k]       
                controller.add_completed( task )
                
            await asyncio.sleep(0.1)


class HpcRunner:

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
            
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"S"}          
        except subprocess.CalledProcessError as e:
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"F"}          

    async def loop(self,controller):
        while True:
            
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
                    if time.time() - p["JOB_ID"]["start"] > 5*60:
                        job_status[p["JOB_ID"]] = {'status':'C'}
                    else:
                        continue

                if job_status[p["JOB_ID"]]['status'] == 'C':
                    console.log("job {} finished".format(p["job"].uid))   
                    to_remove.append(k)

            for k in to_remove:
                del self.processes[k]
                controller.add_completed( p["job"] )
                 
            await asyncio.sleep(2)


EXECUTORS = {    
    "local" : CommandRunner,
    "hpc" : HpcRunner,
}
