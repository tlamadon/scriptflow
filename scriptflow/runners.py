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
import re
import subprocess
from datetime import datetime
from omegaconf import OmegaConf
import os, tempfile
import time

from .container import ContainerSpec, render_command

def exit_code_from_log(log_path):
    """Return the job's exit code by reading the `Exit Code: N` line the job script
    writes at the end of the log, or ``None`` if it can't be determined (log missing,
    not yet flushed, or the job was killed before writing it). Used to detect job
    failures that the scheduler reports only via exit status.
    """
    try:
        with open(log_path) as f:
            text = f.read()
    except OSError:
        return None
    matches = re.findall(r"Exit Code:\s*(-?\d+)", text)
    if matches:
        return int(matches[-1])
    return None


def format_setup(*setups):
    """Combine executor-level and task-level setup into a newline-joined block.

    Each argument may be ``None``, a string, or a list of strings. Empty pieces
    are skipped. The result is injected into the job script after ``module load``
    and before the task command, so jobs can declare their own environment
    (conda activation, ``R_LIBS_USER``, etc.) without inheriting the submitter's
    interactive shell.
    """
    lines = []
    for s in setups:
        if not s:
            continue
        if isinstance(s, str):
            lines.append(s)
        else:
            lines.extend(s)
    return "\n".join(lines)


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
        self.setup = conf.get('setup', [])
        self.container = ContainerSpec.from_conf(conf.get('container', None))
        self.processes = {}

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    """
        Start tasks
    """
    def add(self, task):

        # resolve the command (wrapped into the container if one is configured) and run it
        # through a shell so snippet operators (&&, pipes, ...) and setup lines work.
        line = render_command(task, self.container)
        setup = format_setup(self.setup, task.get_setup())
        inner = setup + "\n" + line if setup else line
        command = ["bash", "-c", inner]

        try:
            if task.quiet:
                subp = subprocess.Popen(command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT)
            else:
                subp = subprocess.Popen(command)
                    #stdout=subprocess.DEVNULL,
                    #stderr=subprocess.STDOUT)
        except OSError as e:
            logging.error("failed to start task %s: %s", task.uid, e)
            return False

        self.processes[task.hash] = {
            "proc" : subp, 
            "task": task , 
            'start_time': datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }
        return True

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
                to_remove.append((k, poll_val))

        for (k, code) in to_remove:
            task = self.processes[k]["task"]
            del self.processes[k]       
            controller.add_completed( task, failed=(code != 0) )


class HpcRunner(AbstractRunner):

    script_template = """\
#!/bin/bash

#PBS -N {name}
#PBS -j oe
#PBS -l procs={procs},mem={mem}gb
#PBS -l walltime={walltime}

echo "Job ID: $PBS_JOBID"
echo "Job Name: $PBS_JOBNAME"
echo "Node: $(hostname)"
echo "Start Time: $(date +'%Y-%m-%d %H:%M:%S')"
echo "----------------------------------------"

{modline}{setup}
cd {wd}

{cmd}
EXIT_CODE=$?

echo "----------------------------------------"
echo "End Time: $(date +'%Y-%m-%d %H:%M:%S')"
echo "Exit Code: $EXIT_CODE"
exit $EXIT_CODE
    """

    def __init__(self, conf):
        conf = OmegaConf.create(conf)
        self.max_proc = conf.maxsize
        self.user = conf.user
        self.modules = conf.modules
        self.walltime = conf.walltime
        self.setup = conf.get('setup', [])
        self.container = ContainerSpec.from_conf(conf.get('container', None))
        self.processes = {}

        # create log-directory
        if not os.path.exists("log"):
            os.mkdir("log")

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    def build_script(self, task):
        # the runtime binary (e.g. apptainer) is a host concern: only emit a module-load
        # line when modules are configured, so an empty string does not produce a bare
        # `module load`.
        modline = f"module load {self.modules}\n" if self.modules else ""
        return self.script_template.format(
            name = "sf-{}".format(task.uid),
            mem = task.mem,
            procs = task.ncore,
            modline = modline,
            walltime = self.walltime,
            wd = os.getcwd(),
            setup = format_setup(self.setup, task.get_setup()),
            cmd = render_command(task, self.container))

    def add(self, task):

        # create the script
        script_content = self.build_script(task)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.sh')
        tmp_script_filename = tmp.name
        tmp.write(script_content.encode())
        tmp.close()

        log_path = f"log/sf-{task.uid}.out"
        task.set_prop("log", log_path)
        command = ["qsub", "-o", log_path, tmp_script_filename]
        try:
            output = subprocess.check_output(command).decode()
            JOB_ID = output.replace("\n","")
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"S", "start": time.time()}          
            return True
        except subprocess.CalledProcessError as e:
            logging.error("qsub failed for task %s: %s", task.uid, e)
            return False

    def update(self, controller):

        # checking job status (filtered to our user)
        output = subprocess.getstatusoutput(f"qstat -u {self.user}")[1]
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
                # grace period: don't mark as completed until 30s after submission
                # (qstat may not show the job immediately after qsub)
                if time.time() - p["start"] < 30:
                    continue
                to_remove.append((k, p))

            elif job_status[p["JOB_ID"]]['status'] == 'C':
                to_remove.append((k, p))

        for (k, p) in to_remove:
            del self.processes[k]
            code = exit_code_from_log(f"log/sf-{p['job'].uid}.out")
            controller.add_completed( p["job"], failed=(code is not None and code != 0) )

    async def loop(self,controller):
        while True:
            self.update(controller)                 
            await asyncio.sleep(2)


class HpcRunner_slurm(AbstractRunner):

    script_template = """\
#!/bin/bash

#SBATCH --account={account} # phd, pi-[faculty], etc.
#SBATCH --partition={partition} # standard, gpu, etc.
#SBATCH --mem-per-cpu={mem}G
#SBATCH --cpus-per-task={ncore} 
#SBATCH --time={walltime} # wall clock limit (d-hh:mm:ss)
#SBATCH --job-name={name} # user-defined job name

# Print job info
echo "Job ID: $SLURM_JOB_ID"
echo "Job User: $SLURM_JOB_USER"
echo "Job Name: $SLURM_JOB_NAME"
echo "Node: $SLURM_NODELIST"
echo "CPUs per Node: $SLURM_JOB_CPUS_PER_NODE"
echo "Memory per CPU: $SLURM_MEM_PER_CPU"
echo "Start Time: $(date +'%Y-%m-%d %H:%M:%S')"
echo "----------------------------------------"

{modline}{setup}
cd {wd}

{cmd}
EXIT_CODE=$?

echo "----------------------------------------"
echo "End Time: $(date +'%Y-%m-%d %H:%M:%S')"
echo "Exit Code: $EXIT_CODE"
exit $EXIT_CODE
    """

    def __init__(self, conf):
        conf = OmegaConf.create(conf)
        self.max_proc = conf.maxsize
        self.processes = {}
        self.user = conf.user
        self.account = conf.account
        self.partition = conf.partition
        self.modules = conf.modules
        self.walltime = conf.walltime
        self.setup = conf.get('setup', [])
        self.container = ContainerSpec.from_conf(conf.get('container', None))

        # create log-directory
        if not os.path.exists("log"):
            os.mkdir("log")

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    def build_script(self, task):
        # the runtime binary (e.g. apptainer) is a host concern: only emit a module-load
        # line when modules are configured, so an empty string does not produce a bare
        # `module load`.
        modline = f"module load {self.modules}\n" if self.modules else ""
        return self.script_template.format(
            name = "{}".format(task.uid),
            mem = task.mem,
            ncore = task.ncore,
            wd = os.getcwd(),
            setup = format_setup(self.setup, task.get_setup()),
            cmd = render_command(task, self.container),
            account = self.account,
            partition = self.partition,
            modline = modline,
            walltime = self.walltime)

    def add(self, task):

        # create the script
        script_content = self.build_script(task)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.sh')
        tmp_script_filename = tmp.name
        tmp.write(script_content.encode())
        tmp.close()

        log_path = f"log/sf-{task.uid}.out"
        task.set_prop("log", log_path)
        command = ["sbatch", "--export=NONE", "-o", log_path, tmp_script_filename]
        try:
            output = subprocess.check_output(command).decode()
            JOB_ID = output.replace("\n","").split(' ')[3]
            self.processes[task.hash] = {"JOB_ID" : JOB_ID, "job": task, "status":"S", "start": time.time()}          
            return True
        except subprocess.CalledProcessError as e:
            logging.error("sbatch failed for task %s: %s", task.uid, e)
            return False

    def update(self, controller):
        # checking job status
        output = subprocess.getstatusoutput(f"squeue --user={self.user}")[1]
        lines = output.split("\n")
        job_status = {}
        for l in lines[1:]:                    
            vals = l.split()
            if len(vals)<3:
                continue
            job_status[vals[0]] = {'status':vals[4]}

        to_remove = []
        for (k,p) in self.processes.items():
            if p["JOB_ID"] not in job_status.keys():
                # grace period: don't mark as completed until 30s after submission
                # (squeue may not show the job immediately after sbatch)
                if time.time() - p["start"] < 30:
                    continue
                to_remove.append((k,p))

        for (k,p) in to_remove:
            del self.processes[k]
            code = exit_code_from_log(f"log/sf-{p['job'].uid}.out")
            controller.add_completed(p["job"], failed=(code is not None and code != 0))

    async def loop(self,controller):
        while True:
            self.update(controller)                 
            await asyncio.sleep(1) 

EXECUTORS = {    
    "local" : CommandRunner,
    "hpc" : HpcRunner,
    "slurm": HpcRunner_slurm,
}