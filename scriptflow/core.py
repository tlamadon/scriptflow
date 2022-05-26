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
import shlex
import fnmatch

from rich.console import Console
from time import sleep
from os.path import exists
from tinydb import TinyDB, Query
from omegaconf import OmegaConf

from .task import Task
from .runners import *
from .glob import *

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

class FileSystem:

    def __init__(self) -> None:
        pass

    def exists(self, filename):
        return exists(filename)

"""
This class creates the link between the user and the different executors.
It also contains the main task logic that doesn't have to do with simply running the command, such 
as checking if the task needs to be run, and wether it has run correctly.
"""
class Controller:

    def __init__(self, conf = {}, runner = None) -> None:
        conf = OmegaConf.create(conf)
        self.history = TinyDB('sf.json')
        self.task_queue = queue.Queue()
        self.done   = 0
        self.retry  = 0
        self.failed = 0
        self.last_notify = ""
        self.notifty_hash = conf.get('notify',None)
        self.fs = FileSystem()
        self.force_string = conf.get('force',None)

        self.shut_me_down = False
        self.loop = None

        self.msg_queue = queue.Queue()

        # we take teh runner if passed (mostly for testing)
        if runner is not None:
            self.executors = { "local": runner }
        elif 'executors' in conf.keys():
            print(conf.executors)
            self.executors = { k:EXECUTORS[k](v) for k,v in conf.executors.items()}
        else:
            self.executors = { "local": CommandRunner({'maxsize':4}) }

    """
    The user tells us that a task should be added
    """
    def add(self, task:Task):

        # check if the task is up to date, 
        # this allows to skip the task right away!
        # if self.check_task_uptodate(task):
        #     self.complete_task(task)
        #     return
        # -> this is done at the time the task is send to the executor

        # otherwise, we add the task to the queue
        self.task_queue.put(task)

    """
    An executor/runner reports a finished task
    """
    def add_completed(self, task:Task) -> None:

        task.set_prop("finish_time", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

        # retry if output file doesn't exist
        if (len(task.get_outputs())>0) and (not exists(task.get_outputs()[0])):

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

                # check if the task is up to date, 
                # this allows to skip the task right away!
                if self.check_task_uptodate(task):
                    self.complete_task(task)
                    return

                # send it to the executor
                task.set_prop('start_time',datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                exec.add(task)
                return

    """
    Check if the task is up to date 
    """
    def check_task_uptodate(self, task):

        # add a test to run if matches regexp (this is to force rerun at least once)
        if (self.force_string is not None) and (fnmatch.fnmatch(task.uid, self.force_string)):
            self.log(f"adding forced [red]{task.uid}[/red]")
            self.log(" - cmd: {}".format( " ".join(task.get_command() ) ))
            return(False)

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
                return(True)

        self.log(f"adding [red]{task.uid}[/red]")
        self.log(" - cmd: {}".format( " ".join(task.get_command() ) ))
        return(False)

    def complete_task(self, task):
        # load the results if any
        # TBD

        self.update_history(task)

        # mark the promise as completed and set it to itself
        assert asyncio.isfuture(task.fut)
        assert not task.fut.done()
        task.set_completed()

    def update_history(self, task):
        # append job to history - replace this with upsert
        # @fixme here sometimes the props don't exist yet, they need to be given a default, or ignored. This happen
        # this happens in particular when skipping the task (then it needs to load first)
        tj = Query()

        newdict = { 
            'deps' : task.deps,
            'output' : task.output_file,
            'cmd': task.get_command()
        }

        # add all available props
        newdict.update(task.props)

        self.history.upsert(newdict, tj.hash == task.hash)

    async def start_loops(self):
        for exec in self.executors.values():
            asyncio.create_task(exec.loop(self))

        # run my own event loop
        with console.status("Running ...") as status:
            while True:

                # logging messages
                while not self.msg_queue.empty():
                    msg = self.msg_queue.get()
                    console.log(msg)

                # check tasks
                self.update()

                # updating status
                running = 0
                capacity = 0
                for exec in self.executors.values():
                    running += exec.size()
                    capacity += exec.size() + exec.available_slots()

                status.update(f"running [green]queued:{self.task_queue.qsize()} [/green] [yellow]running:{running}/{capacity} [/yellow] [purple]done:{self.done} / failed:{self.failed}[/purple] ...")
                # send updates to sever if any
                self.notify(running, capacity)

                await asyncio.sleep(0.1)

    async def update_loop(self):
        while self.shut_me_down==False:
            self.update()
            await asyncio.sleep(0.1)

    async def shutdown(self):
        self.shut_me_down = True
        await self.loop

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

    def set_force_string(self,str):
        self.force_string = str

def init(dict):    
    conf = OmegaConf.create(dict)

    logging.basicConfig(filename='scriptflow.log', level=logging.DEBUG)

    set_main_controller(Controller(conf))
    if conf.debug:
        asyncio.get_event_loop().set_debug(True)