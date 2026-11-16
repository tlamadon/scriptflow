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

import shlex
import fnmatch

from rich.console import Console
from time import sleep
from os.path import exists

from omegaconf import OmegaConf

from .task import Task
from .records import RunRecord
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

class TaskFailed(RuntimeError):
    """Raised at `await` of a task that failed with no retry left."""

class RunCancelled(RuntimeError):
    pass

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
        self.task_queue = queue.Queue()
        self.tasks  = {}
        self.record = None
        self.heartbeat_at = 0
        self.record_failed = False
        self.done   = 0
        self.retry  = 0
        self.failed = 0
        self.fs = FileSystem()
        self.force_string = conf.get('force',None)

        self.shut_me_down = False
        self.loop = None

        self.msg_queue = queue.Queue()
        self.logger = logging.getLogger(self.__class__.__name__)

        # we take teh runner if passed (mostly for testing)
        if runner is not None:
            self.executors = { "local": runner }
        elif 'executors' in conf.keys():
            print(conf.executors)
            self.executors = { k:EXECUTORS[k](v) for k,v in conf.executors.items()}
        else:
            self.executors = { "local": CommandRunner({'maxsize':4}) }

    """
    Start the persisted record of this run, see records.py
    """
    def start_record(self, flow, run_id=None, root=".sf/runs"):
        self.record = RunRecord(flow, run_id, root)
        for task in self.tasks.values():
            self.record_task(task)

    def record_task(self, task, flush=False):
        if self.record is not None:
            self.record.task(task)
            if flush:
                self.record.save()

    # pending changes are written once per tick, the heartbeat every 2s;
    # a failed write is retried at the next heartbeat
    def sync_record(self):
        beat = time.monotonic() >= self.heartbeat_at
        try:
            if beat:
                self.record.heartbeat()
            elif self.record.dirty and not self.record_failed:
                self.record.save()
            self.record_failed = False
        except OSError as e:
            self.record_failed = True
            self.logger.warning("run record not written: %s", e)
        if beat:
            self.heartbeat_at = time.monotonic() + 2

    """
    The user tells us that a task should be added
    """
    def add(self, task:Task):

        if self.shut_me_down:
            raise RunCancelled("controller is stopping")

        # check if the task is up to date, 
        # this allows to skip the task right away!
        # if self.check_task_uptodate(task):
        #     self.complete_task(task)
        #     return
        # -> this is done at the time the task is send to the executor

        # otherwise, we add the task to the queue
        self.tasks[task.execution_id] = task
        task.report("pending")
        self.task_queue.put(task)

    def fail_task(self, task:Task, reason:str) -> None:
        self.failed = self.failed + 1
        task.report("failed", error=reason)
        if not task.fut.done():
            task.fut.set_exception(TaskFailed("{}: {}".format(task.uid, reason)))

    """
    An executor/runner reports a finished task
    """
    def add_completed(self, task:Task, failed:bool=False) -> None:

        task.set_prop("finish_time", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

        # a task is a failure if the job reported a non-zero exit code, or if a
        # declared output file is missing
        output_missing = [f for f in task.get_outputs() if not exists(f)]

        if failed or output_missing:

            reason = "job exited with an error" if failed else "output file missing: " + ", ".join(output_missing)

            if  task.retry>0:
                self.log("[red]{} for {}, retrying... [/red]".format(reason, task.uid))
                task.retry = task.retry - 1
                self.add(task)
            else:
                log_hint = task.props.get("log") if hasattr(task, "props") else None
                hint = " (see [yellow]{}[/yellow])".format(log_hint) if log_hint else ""
                self.log("[red]{} for {}, failing!{}[/red]".format(reason, task.uid, hint))
                self.fail_task(task, reason)

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
            while exec.available_slots()>0 and not self.task_queue.empty():
                # take the next task
                task = self.task_queue.get()

                # check if the task is up to date, 
                # this allows to skip the task right away!
                if self.check_task_uptodate(task):
                    self.complete_task(task, state="skipped")
                    continue

                # send it to the executor (one per tick)
                task.set_prop('start_time',datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                submitted = exec.add(task)
                if submitted is False:
                    self.log("[red]submission failed for {}, failing! (check account/partition and scriptflow.log)[/red]".format(task.uid))
                    self.fail_task(task, "submission failed")
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
        outputs = task.get_outputs()
        if len(outputs)>0 and all(os.path.exists(f) for f in outputs):
            # console.log("checking {}".format(j.output_file))
            output_time = min(os.path.getmtime(f) for f in outputs)

            UP_TO_DATE = True
            for f in task.deps:
                if os.path.getmtime(f) > output_time:    
                    self.log("task [red]{}[/red]: input [green]{}[/green] more recent than output".format(task.uid, f))
                    UP_TO_DATE = False
                    break 
            
            if UP_TO_DATE:
                self.log(f"task [red]{task.uid}[/red] is up to date, skipping it.")
                return(True)
        else:
            self.log(f"adding [red]{task.uid}[/red] output {task.outputs} is missing")

        self.log(f"adding [red]{task.uid}[/red]")
        self.log(" - cmd: {}".format( " ".join(task.get_command() ) ))
        return(False)

    def complete_task(self, task, state="completed"):
        # mark the promise as completed and set it to itself
        assert asyncio.isfuture(task.fut)
        assert not task.fut.done()
        task.set_completed()
        task.report(state)

    async def start_loops(self):
        workers = [asyncio.create_task(exec.loop(self)) for exec in self.executors.values()]
        try:
            await self.main_loop(workers)
        finally:
            for worker in workers:
                worker.cancel()

    async def main_loop(self, workers):

        # run my own event loop
        with console.status("Running ...") as status:
            while True:

                # an executor loop only ends by raising
                for worker in workers:
                    if worker.done():
                        worker.result()

                if self.record is not None:
                    if (self.record.path / "cancel.json").exists():
                        raise RunCancelled("cancellation requested")
                    self.sync_record()

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

                await asyncio.sleep(0.1)

    """
    Stop admitting tasks and cancel what the executors still run.
    Returns True while some job lacks confirmation that it ended.
    """
    async def stop(self):
        self.shut_me_down = True
        unresolved = False
        for exec in self.executors.values():
            try:
                unresolved = bool(exec.cancel()) or unresolved
            except Exception:
                self.logger.exception("executor cleanup failed")
                unresolved = True
        for task in self.tasks.values():
            if not task.fut.done():
                task.fut.cancel()
            elif not task.fut.cancelled():
                # retrieved here so an unawaited failure is not reported at exit
                task.fut.exception()
            if task.state == "pending":
                task.state = "cancelled"
            self.record_task(task)
        return unresolved

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
    called when flow finishes
    """
    def last_word(self):
        while not self.msg_queue.empty():
            console.log(self.msg_queue.get())
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
    if conf.get('debug'):
        asyncio.get_event_loop().set_debug(True)