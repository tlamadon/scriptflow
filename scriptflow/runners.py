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
import os
from pathlib import Path
import signal
import socket
import time
import uuid

from .container import render_command
from .records import now

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

COMMAND_TIMEOUT = 10   # seconds, for any scheduler command
GRACE = 300            # seconds a job may be missing from the scheduler before it is failed
OUTPUT_WAIT = 120      # seconds a finished job's outputs may take to appear on this host
SETTLED = {"completed", "failed", "cancelled", "interrupted"}
CANCEL = {"slurm": "scancel", "pbs": "qdel"}
SLURM_FAILED = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
                "BOOT_FAIL", "DEADLINE", "REVOKED"}


def process_identity(pid):
    """Start time of `pid` from /proc, which tells a process from a later reuse of its pid."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def new_attempt(task, backend):
    """Record one submission of `task`, persisted before anything is launched.

    The log lives in the run directory when the controller keeps a run record, else in
    `log/`. `submission_token` is the scheduler job name, so a submission whose
    acknowledgement was lost can still be found.
    """
    attempt_id = uuid.uuid4().hex
    record = getattr(task.controller, "record", None)
    directory = record.path / "logs" if record is not None else Path("log").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    attempt = {
        "attempt_id": attempt_id, "number": len(task.attempts) + 1,
        "backend": backend, "state": "submitting", "job_id": None,
        "submission_token": "sf-" + attempt_id[:12],
        "submitted_at": now(), "finished_at": None,
        "log": str(directory / (attempt_id + ".out")), "error": None,
        "host": socket.gethostname(),
    }
    task.attempts.append(attempt)
    try:
        task.report("submitting", flush=True, log=attempt["log"])
    except OSError as e:
        attempt.update(state="failed", error=f"could not persist submission intent: {e}")
        raise
    return attempt


def slurm_rows(user):
    """Jobs of `user` as (job_id, job_name, state), recently finished ones included."""
    output = subprocess.check_output(
        ["squeue", "--noheader", "--states=all", f"--user={user}", "--format=%i|%j|%T"],
        timeout=COMMAND_TIMEOUT).decode()
    rows = []
    for line in output.splitlines():
        job_id, name, state = line.strip().split("|")
        if state in ("RUNNING", "COMPLETING"):
            state = "running"
        elif state in ("COMPLETED", "CANCELLED"):
            state = state.lower()
        else:
            state = "failed" if state in SLURM_FAILED else "queued"
        rows.append((job_id, name, state))
    return rows


def pbs_rows():
    """Jobs in `qstat -f` as (job_id, job_name, state)."""
    output = subprocess.check_output(["qstat", "-f"], timeout=COMMAND_TIMEOUT).decode()
    rows = []
    for block in re.split(r"(?m)^Job Id:\s*", output)[1:]:
        job_id, _, details = block.partition("\n")
        name = re.search(r"Job_Name\s*=\s*(\S+)", details)
        state = re.search(r"job_state\s*=\s*(\w+)", details)
        code = re.search(r"exit_status\s*=\s*(-?\d+)", details, re.I)
        if not name or not state:
            raise ValueError("malformed qstat output")
        if state[1] in ("C", "F"):
            # a finished job without exit status is left to the log marker
            if not code:
                continue
            state = "completed" if int(code[1]) == 0 else "failed"
        else:
            state = "running" if state[1] in ("R", "E") else "queued"
        rows.append((job_id.strip(), name[1], state))
    return rows


def settle(attempt, rows, grace=GRACE):
    """Apply one scheduler listing to an attempt; True once it has terminal evidence.

    Evidence is the scheduler's terminal state, else, for a job absent from the listing,
    the `Exit Code` line of its log. A job absent without that line is failed after
    `grace` seconds.
    """
    if attempt["state"] in SETTLED:
        return True
    if attempt["job_id"]:
        found = [r for r in rows if r[0] == attempt["job_id"]]
    else:
        found = [r for r in rows if r[1] == attempt["submission_token"]]
    if len(found) > 1:
        attempt.update(state="unknown", error="several jobs carry this submission token")
        return False

    code = exit_code_from_log(attempt["log"])
    error = None
    if found:
        attempt["job_id"], _, state = found[0]
        attempt.pop("absent_since", None)
        if state == "completed" and code not in (None, 0):
            state = "failed"
    elif code is not None:
        state = "completed" if code == 0 else "failed"
    elif time.time() - attempt.setdefault("absent_since", time.time()) < grace:
        state = "queued" if attempt["job_id"] else "unknown"
    else:
        state, error = "failed", "no scheduler record or exit marker"

    attempt.update(state=state, error=error)
    if state not in SETTLED:
        return False
    attempt.update(exit_code=code, finished_at=now())
    return True


def cancel_attempts(backend, attempts, rows, grace=GRACE):
    """Settle scheduler attempts against `rows` (None: listing failed), cancel the rest.

    Returns the attempts still lacking terminal evidence.
    """
    if rows is None:
        live = [a for a in attempts if a["state"] not in SETTLED]
    else:
        live = [a for a in attempts if not settle(a, rows, grace)]
    known = [a for a in live if a["job_id"]]
    if known:
        try:
            subprocess.check_output([CANCEL[backend]] + [a["job_id"] for a in known],
                                    timeout=COMMAND_TIMEOUT)
            for a in known:
                a["state"] = "cancel_requested"
        except (OSError, subprocess.SubprocessError) as e:
            for a in known:
                a.update(state="unknown", error=str(e))
    return live


def cancel_local(attempt):
    """Terminate a local attempt left by a dead controller; True once its process is gone."""
    if attempt["state"] in SETTLED:
        return True
    pid = attempt.get("pid")
    if not pid or attempt["host"] != socket.gethostname():
        attempt["error"] = "unresolved launch; inspect on " + attempt["host"]
        return False
    if process_identity(pid) != attempt.get("process_identity"):
        attempt.update(state="interrupted", error="process exited without a recorded outcome")
        return True
    os.killpg(pid, signal.SIGTERM)
    attempt["error"] = "termination requested"
    return False


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
        self.processes = {}

    def size(self):
        return(len(self.processes))

    def available_slots(self):
        return self.max_proc - len(self.processes)

    """
        Start tasks
    """
    def add(self, task):

        # resolve the command (wrapped into the task's container if it has one) and run it
        # through a shell so snippet operators (&&, pipes, ...) and setup lines work.
        line = render_command(task)
        setup = format_setup(self.setup, task.get_setup())
        inner = setup + "\n" + line if setup else line
        command = ["bash", "-c", inner]
        try:
            attempt = new_attempt(task, "local")
            with open(attempt["log"], "wb") as log:
                subp = subprocess.Popen(command, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            logging.error("failed to start task %s: %s", task.uid, e)
            if task.attempts:
                task.attempts[-1].update(state="failed", error=str(e), finished_at=now())
            return False

        attempt.update(state="running", pid=subp.pid, process_identity=process_identity(subp.pid))
        self.processes[attempt["attempt_id"]] = {
            "proc" : subp, 
            "task": task , 
            'start_time': datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }
        task.report("running", flush=True)
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
            if not p["task"].quiet and p["task"].attempts:
                self.echo(p)
            poll_val = p["proc"].poll()
            if poll_val is not None:
                to_remove.append((k, poll_val))

        for (k, code) in to_remove:
            task = self.processes[k]["task"]
            del self.processes[k]       
            if task.attempts:
                task.attempts[-1].update(state="completed" if code == 0 else "failed",
                    exit_code=code, finished_at=now())
            controller.add_completed( task, failed=(code != 0) )

    def echo(self, p):
        """Print what a non-quiet task wrote to its log since the last call."""
        with open(p["task"].attempts[-1]["log"], "rb") as log:
            log.seek(p.get("offset", 0))
            chunk = log.read()
            p["offset"] = log.tell()
        if chunk:
            print(chunk.decode(errors="replace"), end="", flush=True)

    def cancel(self):
        """Terminate running process groups; True if any could not be confirmed dead."""
        unresolved = False
        for (k, p) in list(self.processes.items()):
            proc, task = p["proc"], p["task"]
            try:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=1)
                task.attempts[-1].update(state="cancelled", finished_at=now())
                task.report("cancelled")
                del self.processes[k]
            except (OSError, subprocess.SubprocessError) as e:
                task.attempts[-1].update(state="unknown", error=str(e))
                task.report("unknown")
                unresolved = True
        return unresolved


class SchedulerRunner(AbstractRunner):
    """Submission, polling and cancellation shared by the PBS and Slurm runners."""

    def add(self, task):
        try:
            attempt = new_attempt(task, self.backend)
        except OSError as e:
            logging.error("cannot record task %s: %s", task.uid, e)
            return False

        script = Path(attempt["log"]).with_suffix(".sh")
        command = self.submit_command(attempt) + ["-o", attempt["log"], str(script)]
        try:
            script.write_text(self.build_script(task))
            output = subprocess.check_output(command, timeout=COMMAND_TIMEOUT).decode()
            job_id = self.job_id_pattern.search(output)
        except (subprocess.CalledProcessError, OSError) as e:
            logging.error("%s failed for task %s: %s", command[0], task.uid, e)
            attempt.update(state="failed", error=str(e), finished_at=now())
            return False
        except subprocess.TimeoutExpired:
            job_id = None

        if job_id:
            attempt.update(job_id=job_id[1], state="queued")
        else:
            # the scheduler may hold the job: the next poll finds it by name
            attempt.update(state="unknown", error="no submission acknowledgement")

        self.processes[attempt["attempt_id"]] = {"job": task, "attempt": attempt}
        task.report(attempt["state"], flush=True)
        return True

    def poll(self):
        """One scheduler listing as (rows, error)."""
        try:
            return self.fetch(), None
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return None, str(e)

    def apply(self, controller, rows, error):
        for (k, p) in list(self.processes.items()):
            task, attempt = p["job"], p["attempt"]
            before = (attempt["state"], attempt["job_id"])
            if rows is None:
                attempt.update(state="unknown", error=error)
            elif settle(attempt, rows, self.grace):
                if attempt["state"] == "completed" and not self.outputs_arrived(p):
                    continue
                del self.processes[k]
                controller.add_completed(task, failed=(attempt["state"] != "completed"))
                continue
            if before != (attempt["state"], attempt["job_id"]):
                if attempt["state"] == "unknown":
                    controller.log("[yellow]scheduler status unknown for {}: {}[/yellow]".format(task.uid, attempt["error"]))
                task.report(attempt["state"])

    def outputs_arrived(self, p):
        """True once the task's outputs are visible here, or `output_wait` seconds have passed.

        A file written on another node can lag on a network filesystem; listing its
        directory makes the client look again.
        """
        deadline = p.setdefault("outputs_by", time.time() + self.output_wait)
        outputs = p["job"].get_outputs()
        for directory in {os.path.dirname(f) or "." for f in outputs if not os.path.exists(f)}:
            try:
                os.listdir(directory)
            except OSError:
                pass
        return all(os.path.exists(f) for f in outputs) or time.time() >= deadline

    def update(self, controller):
        self.apply(controller, *self.poll())

    async def loop(self, controller):
        while True:
            # the listing runs in a thread; attempts are only touched on the event loop
            if self.processes:
                result = await asyncio.get_event_loop().run_in_executor(None, self.poll)
                self.apply(controller, *result)
            await asyncio.sleep(self.interval)

    def cancel(self):
        """Cancel active jobs; True while any lacks terminal evidence."""
        rows, _ = self.poll()
        attempts = [p["attempt"] for p in self.processes.values()]
        cancel_attempts(self.backend, attempts, rows, self.grace)
        for (k, p) in list(self.processes.items()):
            if p["attempt"]["state"] in SETTLED:
                del self.processes[k]
            p["job"].report(p["attempt"]["state"])
        return bool(self.processes)


class HpcRunner(SchedulerRunner):

    backend = "pbs"
    interval = 2
    job_id_pattern = re.compile(r"^\s*(\S+)\s*$")
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
        self.grace = conf.get('grace', GRACE)
        self.output_wait = conf.get('output_wait', OUTPUT_WAIT)
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
            cmd = render_command(task))

    def submit_command(self, attempt):
        return ["qsub", "-N", attempt["submission_token"]]

    def fetch(self):
        return pbs_rows()

class HpcRunner_slurm(SchedulerRunner):

    backend = "slurm"
    interval = 1
    job_id_pattern = re.compile(r"Submitted batch job (\d+)")
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
        self.grace = conf.get('grace', GRACE)
        self.output_wait = conf.get('output_wait', OUTPUT_WAIT)

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
            cmd = render_command(task),
            account = self.account,
            partition = self.partition,
            modline = modline,
            walltime = self.walltime)

    def submit_command(self, attempt):
        return ["sbatch", "--export=NONE", "--job-name", attempt["submission_token"]]

    def fetch(self):
        return slurm_rows(self.user)

EXECUTORS = {    
    "local" : CommandRunner,
    "hpc" : HpcRunner,
    "slurm": HpcRunner_slurm,
}