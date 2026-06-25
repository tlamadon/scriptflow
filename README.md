# scriptflow

A lightweight Python library for scheduling and orchestrating computational pipelines on HPC clusters. If you don't like writing complicated makefiles but can write a for-loop in Python, scriptflow might be just what you're looking for.

Define task dependencies as Python code using `async`/`await`, and let scriptflow handle job submission, output tracking, and caching.

## Installation

### 1. Set up a virtual environment

On the cluster head node:

```bash
python3 -m venv env
source env/bin/activate
pip install --upgrade pip
```

### 2. Install scriptflow

```bash
pip install git+https://github.com/tlamadon/scriptflow.git@ddml-jel
```

Or install in development mode:

```bash
git clone -b ddml-jel https://github.com/tlamadon/scriptflow.git
cd scriptflow
pip install -e .
```

### Requirements

- Python ≥ 3.12

### Loading the environment

Every time you want to use scriptflow, activate the virtual environment first:

```bash
source env/bin/activate
```

You do not need to reinstall scriptflow each time.

## Quick Start

### 1. Create `sflow.py`

In your project directory, create a file called `sflow.py`:

```python
import scriptflow as sf

sf.init({
    "executors": {
        "slurm": {
            "maxsize": 50,              # max concurrent Slurm jobs
            "account": "my-account",    # Slurm account (--account)
            "user": "myuser",           # cluster username (for squeue polling)
            "partition": "standard",    # Slurm partition (--partition)
            "modules": "R/4.5",        # modules loaded on compute nodes
            "walltime": "1-00:00:00"   # wall clock limit (d-hh:mm:ss)
        }
    },
    "debug": False
})

async def flow_analysis():
    # Phase 1: Run 10 tasks in parallel
    tasks = [
        sf.Task(
            cmd=f"Rscript --vanilla myscript.R {i}",
            outputs=f"output/result_{i}.csv",
            name=f"task_{i}"
        ).set_cpu(1).set_memory(8)
        for i in range(1, 11)
    ]
    await sf.bag(*tasks)

    # Phase 2: Aggregate (runs after all Phase 1 tasks complete)
    await sf.Task(
        cmd="Rscript --vanilla aggregate.R",
        outputs="output/final_table.tex",
        name="aggregate"
    ).set_cpu(1).set_memory(4)
```

### 2. Run a flow

```bash
scriptflow run analysis
```

This discovers `flow_analysis()` in `sflow.py` and executes it.

## Core Concepts

### Tasks

A `Task` wraps a shell command with metadata:

```python
task = sf.Task(
    cmd="Rscript --vanilla myscript.R arg1 arg2",  # shell command
    outputs="path/to/output_file.csv",              # expected output file(s)
    name="descriptive_name"                         # task identifier
)
```

Resource configuration via chaining:

```python
task.set_cpu(4)       # CPUs per task (--cpus-per-task)
task.set_memory(16)   # memory in GB per CPU (--mem-per-cpu)
task.set_retry(2)     # retry count on failure
```

If the output file already exists (and is newer than the inputs), the task is **skipped automatically**.

### Per-task environment setup

Jobs run in a **clean environment** (the Slurm runner submits with `--export=NONE`), so a job
does not inherit whatever you have loaded/activated in your interactive shell. This keeps runs
reproducible: everything a job needs is declared in the pipeline, not implicit in your session.

Beyond `module load` (set via the executor's `modules`), a task can declare extra setup commands
that run **inside the job, after `module load` and before the command** — handy for activating a
conda environment, pointing R at a user library, or exporting variables:

```python
sf.Task(
    cmd="python fine_tune.py",
    name="fine-tune",
    setup=[
        "source $HOME/miniconda3/etc/profile.d/conda.sh",
        "conda activate myenv",
    ],
)

# or via chaining
sf.Task(cmd="Rscript estimate.R", name="est").set_setup("export R_LIBS_USER=$HOME/R/library")
```

You can also set a shared `setup` block at the executor level (applied to every job, before each
task's own `setup`):

```python
sf.init({
    "executors": {
        "slurm": {
            "maxsize": 50, "account": "my-account", "user": "myuser",
            "partition": "standard", "modules": "R/4.5", "walltime": "1-00:00:00",
            "setup": ["export R_LIBS_USER=$HOME/R/library"],   # applies to all jobs
        }
    }
})
```

### Flows

Flows are `async` functions prefixed with `flow_`. Use `await` for sequential dependencies and `sf.bag()` for parallel execution:

```python
async def flow_pipeline():
    # Parallel: all tasks run simultaneously
    await sf.bag(task_a, task_b, task_c)

    # Sequential: runs only after the bag above completes
    await task_d
```

### Executors

Scriptflow supports three execution backends:

| Executor | Key | Submits via | Status |
|---|---|---|---|
| **Slurm** | `"slurm"` | `sbatch` | ✅ Stable |
| Local | `"local"` | `subprocess` | ✅ Stable (for testing) |
| PBS | `"hpc"` | `qsub` | ✅ Stable |

**Slurm executor configuration:**

```python
sf.init({
    "executors": {
        "slurm": {
            "maxsize": 120,                  # max concurrent jobs
            "account": "my-account",         # --account
            "user": "myuser",               # for squeue polling
            "partition": "standard",         # --partition
            "modules": "R/4.5 python/3.12", # modules loaded on compute nodes
            "walltime": "1-23:59:59"         # --time (d-hh:mm:ss)
        }
    },
    "debug": False
})
```

**Local executor** (useful for testing pipelines without submitting to a cluster):

```python
sf.init({
    "executors": {
        "local": {"maxsize": 4}
    },
    "debug": True
})
```

## Project Files

When you run scriptflow, the following files are created in your project directory:

| File/Dir | Purpose |
|---|---|
| `sflow.py` | Your pipeline definition (you create this) |
| `log/` | One `.out` file per job with stdout/stderr |
| `scriptflow.log` | Internal debug log |

To remove all auto-generated files and start fresh:

```bash
scriptflow clean
```

## Using tmux

Since scriptflow runs on the head node and monitors job progress, use [tmux](http://tmuxcheatsheet.com/) to keep it running after you disconnect:

```bash
# Start a new tmux session
tmux

# Activate your venv and run scriptflow as usual
source env/bin/activate
cd my_project/
scriptflow run analysis

# Detach from the session (scriptflow keeps running):
#   press Ctrl+B, then D

# Reconnect later:
tmux a -t 0
```

## Example

See [`examples/Rscript/`](examples/Rscript/) for a complete working example that runs R simulations on a Slurm cluster and aggregates the results.
