# Design: task-based containers

**Status:** proposal / discussion
**Scope:** `scriptflow/container.py`, `scriptflow/task.py`, `scriptflow/runners.py`,
`scriptflow/__init__.py`, tests.

## 1. Problem

Today a container is configured **once per executor**. A task can only either use that
one image or opt out entirely (`container=False`). There is no way to say "run *this*
task in a *different* image with its own binds/env".

A pipeline that has one main analysis image plus a second image for one stage (e.g. a
data-prep image) is therefore forced to opt the stage out of the container and then
**re-implement container wrapping by hand**. Concretely, in the Intro_DML_Replication
package the data-prep stage does this:

```python
def dataprep(cmd):
    binds = (f"--bind {DATAPREP_CODE}:/work "
             f"--bind {DATAPREP_REPL}:/repl:ro "
             f"--bind {DATAPREP_OUT}:/out "
             f"--bind {DATAPREP_DATA}:/data")
    return (
        f"mkdir -p {DATAPREP_OUT} {DATAPREP_DATA} && "
        f"apptainer exec --cleanenv --no-home {binds} "
        f"--env REPL_DIR=/repl --env OUT_DIR=/out --env DATA_DIR=/data --pwd /work "
        f"{DATAPREP_IMAGE} {cmd}"
    )
# ... then every data-prep task passes container=False and wraps its cmd with dataprep().
```

This duplicates exactly what `ContainerSpec` already does (runtime, flags, binds,
workdir), is easy to get wrong (quoting, `--pwd` vs `cd`), and buries the container
contract in the user's flow file instead of the framework.

## 2. Current architecture (for reference)

- **`ContainerSpec`** (`container.py`): wraps one shell snippet into an
  `apptainer exec … bash -c '<snippet>'` argv. Fields: `image`, `runtime`, `binds`,
  `flags`, `workdir`. Built from an executor sub-config via `ContainerSpec.from_conf`.
  It has **no `env` support**.
- **`render_command(task, container)`** (`container.py`): returns the wrapped line when
  `container is not None and task.use_container`, else the raw snippet.
- **`Task.use_container`** (`task.py`): a single bool from the `container=` kwarg
  (default `True`). This is the *only* per-task container control today.
- **Runners** (`runners.py`): each runner builds one `self.container =
  ContainerSpec.from_conf(conf.get('container'))` and calls `render_command(task,
  self.container)` in `add()` / `build_script()`.

Key invariant to preserve: a task command is a **shell snippet** carried verbatim as the
single `bash -c` payload, so operators (`&&`, pipes) and quoting survive (see
`container.py` module docstring and `tests/test_container.py`).

## 3. Goals

1. **Per-task containers are the canonical mechanism.** A task declares the container it
   runs in (or none = host). There is **no implicit executor-level default container** —
   nothing is silently inherited (see §4.7).
2. Containers are **modular, first-class objects** (`ContainerSpec`) reused across tasks,
   so a flow file reads as *host → containers → workflows*.
3. `ContainerSpec` gains **environment-variable** support (needed to retire the manual
   `--env` wrapping above).
4. **Remove the executor-level `container` block entirely** (§4.7). Containers live only
   on tasks. This is a deliberate breaking change: a flow that relied on the executor
   default must now name the container on each task.
5. No change to the shell-snippet invariant.

## 4. Proposed design

### 4.1 `container=` accepts a spec, a dict, or nothing

The `Task` `container=` kwarg is the **only** place a container is chosen:

| value | meaning |
|---|---|
| absent / `None` / `False` | run on the **host** (no container) |
| `ContainerSpec` | run in that container |
| `dict` | run in `ContainerSpec.from_conf(dict)` |

The default is **host** (no container). `False` (the old opt-out) and bare `True` are both
accepted and both mean host — `True` no longer has anything to inherit, so it is treated
as host rather than raising, which keeps old opt-out/opt-in flows from crashing (they
simply run on the host until migrated to a per-task spec).

Parsing in `Task.__init__` (replacing the current `use_container` line):

```python
c = kwargs.get("container", None)
if c is None or c is False or c is True:
    self.container_spec = None            # host (no executor default to inherit)
elif isinstance(c, ContainerSpec):
    self.container_spec = c
elif isinstance(c, dict):
    self.container_spec = ContainerSpec.from_conf(c)
else:
    raise TypeError("container= must be a ContainerSpec, dict, or None")
```

`bool` is checked with `is` (not `isinstance`) before `dict`/`ContainerSpec` so a truthy
dict/spec is never mistaken for `True`. `self.use_container` is gone entirely.

### 4.2 `render_command` takes only the task

With no executor container, the fallback argument disappears:

```python
def render_command(task):
    cmd = task.get_command_str()
    spec = getattr(task, "container_spec", None)
    return shlex.join(spec.wrap(cmd)) if spec is not None else cmd
```

Resolution is simply: **task spec → host.** A task runs in a container iff it carries a
`ContainerSpec`; otherwise it runs on the host. The runtime binary must be available on
the host (via executor `modules`/`setup`) exactly as before.

### 4.3 `ContainerSpec` gains `env`

Add an `env` mapping rendered as repeated `--env K=V` (apptainer/singularity syntax;
docker is out of scope as it is today):

```python
def __init__(self, image, runtime="apptainer", binds=None, flags=None,
             workdir="", env=None):
    ...
    self.env = dict(env) if env else {}

def wrap(self, cmd):
    inner = f"cd {shlex.quote(self.workdir)} && {cmd}" if self.workdir else cmd
    argv = [self.runtime, "exec", *self.flags]
    for b in self.binds:
        argv += ["--bind", b if ":" in b else f"{b}:{b}"]
    for k, v in self.env.items():
        argv += ["--env", f"{k}={v}"]
    argv += [self.image, "bash", "-c", inner]
    return argv
```

`from_conf` also reads `env` (default `{}`). Final `shlex.join` in `render_command`
quotes each token, so values with spaces are safe.

### 4.4 A `ContainerSpec` is self-contained (no inheritance)

Because there is no executor default, there is nothing to inherit or merge: a
`ContainerSpec` is a complete, self-contained description of how to run (image, runtime,
binds, flags, workdir, env). Two tasks that share an image just share the same spec
object. If an "inherit + override" helper is ever wanted it will be added explicitly
(e.g. `base.merged_with(...)`), never as an implicit default.

### 4.5 Bind-source existence is not a container concern

A container runtime requires each **host bind *source*** to exist before `exec`. This is
a property of the runtime, **not** of scriptflow:

- A container that binds an already-present directory (the checked-out project, a data
  root the user populated) needs nothing extra — the source pre-exists.
- A container that binds a directory the pipeline itself produces (an output/intermediate
  dir) faces the same rule: that source must exist first.

Design decision: **`ContainerSpec` only wraps a command** (binds, flags, env, workdir).
It deliberately does **not** create directories. Provisioning a not-yet-existing bind
source is an ordinary pipeline step, expressed via the per-task `setup=` lines (host
shell, run before the container line):

```python
sf.Task(cmd="python build_ipeirotis.py",
        setup=[f"mkdir -p {DATAPREP_OUT} {DATAPREP_DATA}"],
        container=DATAPREP_CONTAINER)
```

An automatic "mkdir every bind source" option was considered and **rejected**: it cannot
distinguish an *output* dir (create it) from a missing *input* dir (should fail loudly),
so it would mask real errors.

### 4.6 Export `ContainerSpec` from the package

Add `ContainerSpec` to `scriptflow/__init__.py` so flows can write
`container=sf.ContainerSpec(...)` without importing from `scriptflow.container`.
(A dict is also accepted per §4.1, so the export is convenience, not required.)

### 4.7 The executor-level `container` block is removed

The executor holds **only host/scheduler settings** (account, partition, walltime,
modules, maxsize, setup). The `container` key is **no longer read** by any runner:

- Each runner drops `self.container = ContainerSpec.from_conf(conf.get('container'))`.
- Each runner calls `render_command(task)` (no container argument).

If a `container` block is left in an executor config it is silently ignored. Every task
that needs a container names one; tasks that name none run on the host. `ContainerSpec.from_conf`
stays — it now parses only the per-task `dict` form (§4.1).

## 5. Recommended flow-file layout (downstream: Intro_DML_Replication `sflow.py`)

Structure the flow file top-to-bottom as **host → containers → workflows**:

```python
import scriptflow as sf
from config import *

# 1. Host — scheduler settings only, no container.
HOST = {"maxsize": SLURM_MAXSIZE, "account": SLURM_ACCOUNT, "user": SLURM_USER,
        "partition": SLURM_PARTITION, "modules": SLURM_MODULES, "walltime": SLURM_WALLTIME}

# 2. Containers — the two provided images, as first-class objects.
MAIN_CONTAINER = sf.ContainerSpec(
    image=APPTAINER_IMAGE, binds=APPTAINER_BINDS, flags=APPTAINER_FLAGS,
    workdir=PROJECT_ROOT)

DATAPREP_CONTAINER = sf.ContainerSpec(
    image=DATAPREP_IMAGE, flags=["--cleanenv", "--no-home"],
    binds=[f"{DATAPREP_CODE}:/work", f"{DATAPREP_REPL}:/repl:ro",
           f"{DATAPREP_OUT}:/out", f"{DATAPREP_DATA}:/data"],
    workdir="/work",
    env={"REPL_DIR": "/repl", "OUT_DIR": "/out", "DATA_DIR": "/data"})

sf.init({"executors": {"slurm": HOST}, "debug": False})   # no default container

# 3. Workflows — every task names its container.
```

Analysis task — names the main image explicitly (no inherited default):

```python
await sf.Task(
    cmd = f"cd 401k && Rscript --vanilla repl_run_sim.R {i} {SEED_VEC_401K[i]} {NSIM_401K}",
    outputs = f"401k/temp/sim_res_{i}.csv",
    name = f"401k_{i}",
    container = MAIN_CONTAINER,
).set_retry(0).set_cpu(1).set_memory(8)
```

Data-prep task — data-prep image + host-side `mkdir` via `setup=`:

```python
await sf.Task(
    cmd = "python build_ipeirotis.py",
    outputs = [...],
    name = "monopsony_dataprep_py",
    setup = [f"mkdir -p {DATAPREP_OUT} {DATAPREP_DATA}"],
    container = DATAPREP_CONTAINER,
).set_retry(0).set_cpu(1).set_memory(16)
```

Every task states its environment; the `dataprep()` helper, the `container=False`
opt-out, and any implicit default all disappear. The one cost of "no default" is that
each analysis task passes `container=MAIN_CONTAINER` — accepted in exchange for having
nothing inherited.

## 6. Implementation plan (by file)

1. **`container.py`**
   - `ContainerSpec.__init__`: add `env=None` → `self.env`.
   - `wrap`: emit `--env K=V` per entry (after binds, before image).
   - `from_conf`: read `env` (default `{}`); keep it for the per-task `dict` form.
   - `render_command(task)`: drop the executor-container argument; wrap iff
     `task.container_spec` is set (§4.2).
   - Update the module docstring: whether-to-wrap is now decided by the task's
     `container_spec`, not by executor config; document `env`.
2. **`task.py`**
   - Replace the `self.use_container = bool(...)` line with the parsing in §4.1, setting
     `self.container_spec` (and removing `use_container` — nothing reads it anymore).
   - Import `ContainerSpec` from `.container`.
   - Update the `container=` doc comment (host by default; pass a spec/dict to containerize).
3. **`runners.py`** — in all three runners (`CommandRunner`, `HpcRunner`,
   `HpcRunner_slurm`):
   - delete `self.container = ContainerSpec.from_conf(conf.get('container', None))`;
   - change `render_command(task, self.container)` → `render_command(task)`.
   - `ContainerSpec` import stays only if still referenced; otherwise drop it.
4. **`__init__.py`** — export `ContainerSpec`.
5. **Tests** (`tests/test_container.py`) — rewrite around per-task containers:
   - `env` renders as `--env K=V` in `wrap` and via `from_conf`.
   - a task with a `ContainerSpec` (and a task with a `dict`) is wrapped correctly.
   - a task with no `container` / `False` / `True` runs on the **host** (no wrapping).
   - `render_command(task)` on a host task returns the raw snippet; on a container task
     returns the `apptainer exec … bash -c '<snippet>'` line with operators/quoting intact.
   - a slurm/pbs `build_script` embeds the task image, binds, and env.
   - `container=` with a bad type raises `TypeError`.
   - **update/remove** existing tests that configured an executor-level `container` block
     (that path no longer exists).

## 7. Compatibility (breaking)

This is intentionally **not** backward compatible:

- The executor-level `container` block is **removed**; a config that still sets it is
  ignored, so those tasks now run on the host until migrated to per-task specs.
- `container=True` no longer inherits anything — it means host. `container=False`/absent
  are unchanged (host).
- `render_command` loses its second argument; any external caller must update.
- `ContainerSpec(...)` without `env`: identical argv to today (empty `env` adds nothing).

## 8. Decisions & open questions

- **Executor-level `container` block** (§4.7): **DECIDED — removed entirely.** Containers
  live only on tasks; no implicit default, nothing inherited. Costs one explicit
  `container=MAIN_CONTAINER` per analysis task; accepted.
- **Merge/replace** (§4.4): **DECIDED — moot.** With no executor default there is nothing
  to merge; a `ContainerSpec` is self-contained.
- **Bind-source existence** (§4.5): **RESOLVED BY DESIGN — not a container concern.**
  `ContainerSpec` never creates directories; bind sources pre-exist or are provisioned via
  `setup=`.

Still open:

1. **Env representation**: repeated `--env K=V` vs. `--env-file`. Repeated flags are
   simpler and match the current manual helper; proposed default.
2. **Docker runtime**: `wrap()` targets `apptainer/singularity exec` today; `--env` is
   compatible with both. Docker (`run` vs `exec`) remains out of scope — confirm.
