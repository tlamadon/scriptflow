import click
import asyncio

# loading the use file
import importlib.util
from inspect import getmembers, isfunction

import scriptflow.core as sf
import os
import fcntl
import getpass
import json
import signal
import subprocess

from .glob import get_main_controller
from .records import (TERMINAL, atomic_json, inspect_record, now, read_record,
                      request_cancel, run_path)
from .runners import SETTLED, cancel_attempts, cancel_local, pbs_rows, slurm_rows

# loading the local sflow.py file
def load_local_file(filename):
    spec = importlib.util.spec_from_file_location("", os.getcwd() + "/" + filename)
    foo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(foo)
    return foo

"""
    Main
"""
async def main(func, controller:sf.Controller):

    task_controller = asyncio.create_task( controller.start_loops() )
    task_flow = asyncio.create_task( func() )

    # SIGINT/SIGTERM cancel this coroutine, so that running jobs are cancelled below
    loop = asyncio.get_running_loop()
    signals = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, asyncio.current_task().cancel)
            signals.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    os.makedirs('.sf', exist_ok=True)

    state, error = "completed", None
    try:
        await asyncio.wait([task_flow, task_controller], return_when=asyncio.FIRST_COMPLETED)
        if task_controller.done():
            await task_controller
            raise RuntimeError("controller stopped before the flow completed")
        await task_flow

        if controller.failed:
            raise sf.TaskFailed("{} task(s) failed".format(controller.failed))
        if any(not t.fut.done() for t in controller.tasks.values()):
            raise sf.TaskFailed("flow returned with unawaited tasks")

        # let the other guys finish
        await asyncio.sleep(1)
    except sf.RunCancelled as e:
        state, error = "cancelled", str(e)
        raise
    except asyncio.CancelledError:
        state, error = "interrupted", "controller interrupted"
        raise
    except BaseException as e:
        state, error = "failed", str(e)
        raise
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        task_flow.cancel()
        task_controller.cancel()
        await asyncio.gather(task_flow, task_controller, return_exceptions=True)
        cleanup_pending = await controller.stop() if state != "completed" else False
        if controller.record is not None:
            try:
                controller.record.finish(state, error, cleanup_pending)
            finally:
                controller.record.close()
        controller.last_word()


@click.group()
def cli():
    pass

@cli.command()
@click.argument('name')
@click.option('-f', '--force', 'force')
@click.option('--run-id', help='Identity of this launch; an existing one is rejected.')
@click.option('--runs-dir', default='.sf/runs', show_default=True, type=click.Path())
def run(name="",force=None,run_id=None,runs_dir=".sf/runs",load="sflow.py"):

    # an existing run is rejected before the flow file is imported
    if run_id is not None:
        try:
            taken = run_path(run_id, runs_dir).exists()
        except ValueError as e:
            raise click.ClickException(str(e))
        if taken:
            raise click.ClickException("Run '{}' already exists".format(run_id))

    foo = load_local_file("sflow.py")

    func_names = {k:v for (k,v) in getmembers(foo, isfunction)}
    flows = [w.replace("flow_","") for w in func_names.keys() if w.startswith("flow_")]

    if name not in flows:
        raise click.ClickException("Flow '{}' is not available, choose from: {}".format(name, ", ".join(flows)))

    func = func_names["flow_{}".format(name)]

    controller = get_main_controller()
    if controller is None:
        raise click.ClickException("sflow.py must call scriptflow.init()")
    controller.set_force_string(force)

    try:
        controller.start_record(name, run_id, runs_dir)
    except (OSError, ValueError) as e:
        raise click.ClickException("cannot start run record: {}".format(e))
    click.echo("Run: {}".format(controller.record.path.name))

    try:
        asyncio.run(main(func, controller))
    except asyncio.CancelledError:
        raise click.ClickException("interrupted; inspect the run for remaining jobs")
    except Exception as e:
        raise click.ClickException(str(e))

@cli.command("inspect")
@click.argument('run_id')
@click.option('--runs-dir', default='.sf/runs', show_default=True, type=click.Path())
@click.option('--json', 'as_json', is_flag=True)
def inspect_run(run_id, runs_dir, as_json):
    """Print a run record; reads files only, no scheduler query."""
    try:
        data = inspect_record(run_path(run_id, runs_dir))
    except (OSError, ValueError) as e:
        raise click.ClickException(str(e))
    if as_json:
        click.echo(json.dumps(data))
        return
    click.echo("{}: {} ({} tasks)".format(data["run_id"], data["state"], len(data["tasks"])))
    if data["observation"]["stale"]:
        click.echo("Controller heartbeat is stale; the outcome is unknown.")
    if data["cleanup_pending"]:
        click.echo("Job cleanup is unresolved.")
    if data["error"]:
        click.echo(data["error"])

@cli.command()
@click.argument('run_id')
@click.option('--runs-dir', default='.sf/runs', show_default=True, type=click.Path())
def cancel(run_id, runs_dir):
    """Ask the run's controller to stop and cancel its jobs."""
    try:
        data = request_cancel(run_path(run_id, runs_dir))
    except (OSError, ValueError) as e:
        raise click.ClickException(str(e))
    if data["state"] in TERMINAL:
        click.echo("Run is {}; use cleanup if jobs remain.".format(data["state"]))
    else:
        click.echo("Cancellation requested; inspect the run to confirm.")

@cli.command()
@click.argument('run_id')
@click.option('--runs-dir', default='.sf/runs', show_default=True, type=click.Path())
def cleanup(run_id, runs_dir):
    """Cancel the jobs left by a controller that has stopped."""
    try:
        path = run_path(run_id, runs_dir)
        with (path / "controller.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("controller is active; use cancel instead")
            data = read_record(path)
            pending = cleanup_attempts(data)
            if data["state"] not in TERMINAL:
                data.update(state="interrupted", finished_at=now(),
                            error="controller stopped without a final record")
            data.update(cleanup_pending=pending, updated_at=now())
            atomic_json(path / "run.json", data)
    except (OSError, ValueError) as e:
        raise click.ClickException(str(e))
    if pending:
        raise click.ClickException("cleanup unresolved; inspect attempts and retry")
    click.echo("Cleanup complete; the flow has not been resumed.")

def cleanup_attempts(data):
    """One scheduler listing and one cancel command per backend; True if a job may remain."""
    attempts = [a for t in data["tasks"].values() for a in t["attempts"]]
    live = [a for a in attempts if a["backend"] == "local" and not cancel_local(a)]
    for backend in ("slurm", "pbs"):
        mine = [a for a in attempts if a["backend"] == backend and a["state"] not in SETTLED]
        if mine:
            try:
                rows = slurm_rows(getpass.getuser()) if backend == "slurm" else pbs_rows()
            except (OSError, subprocess.SubprocessError, ValueError):
                rows = None
            live += cancel_attempts(backend, mine, rows, grace=0)
    for task in data["tasks"].values():
        if task["state"] not in ("completed", "skipped", "failed", "cancelled"):
            task["state"] = "cancelled"
    return bool(live)

@cli.command()
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation prompt')
def clean(yes):
    """Remove auto-generated files: log/, scriptflow.log, .sf/"""
    import shutil

    targets = [
        ('scriptflow.log', 'file'),
        ('log', 'dir'),
        ('.sf', 'dir'),
    ]

    found = [(path, kind) for path, kind in targets if os.path.exists(path)]

    if not found:
        click.echo("Nothing to clean.")
        return

    click.echo("Will remove:")
    for path, kind in found:
        if kind == 'dir':
            n = len(os.listdir(path))
            click.echo(f"  {path}/ ({n} files)")
        else:
            size = os.path.getsize(path)
            click.echo(f"  {path} ({size:,} bytes)")

    if not yes:
        click.confirm("Proceed?", abort=True)

    for path, kind in found:
        if kind == 'dir':
            shutil.rmtree(path)
        else:
            os.remove(path)
        click.echo(f"  Removed {path}")

    click.echo("Done.")

# if __name__ == '__main__':
#     cli()