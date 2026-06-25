"""
Example: Running R simulations on an HPC cluster.

Uncomment the executor block for your cluster's scheduler, then run:
> scriptflow run Rit
"""

import scriptflow as sf
import os

# ==============================================================================
# Choose ONE executor below, and adapt parameters to your cluster.
# ==============================================================================

# --- Option 1: Slurm ---
sf.init({
    "executors": {
        "slurm": {
            "maxsize": 2,
            "account": "my-account",   # your Slurm account
            "user": "myuser",          # your cluster username
            "partition": "standard",
            "modules": "R/4.5",
            "walltime": "00:05:00"
        }
    },
    "debug": True
})

# --- Option 2: PBS ---
# sf.init({
#     "executors": {
#         "hpc": {
#             "maxsize": 2,
#             "user": "myuser",
#             "modules": "R/4.5",
#             "walltime": "00:05:00"
#         }
#     },
#     "debug": True
# })

# --- Option 3: Local (for testing) ---
# sf.init({
#     "executors": {
#         "local": {"maxsize": 5}
#     },
#     "debug": True
# })

# ==============================================================================
# Flow
# ==============================================================================

temp_dir = "temp"
os.makedirs(temp_dir, exist_ok=True)

async def flow_Rit():
    """Generate 5 simulation draws, then aggregate."""

    # Phase 1: Generate simulation draws (parallel)
    #
    # Jobs run in a clean environment, so declare anything beyond `modules` here.
    # `setup` lines run inside the job, after `module load` and before the command.
    # (Remove/adapt the example below to match your cluster.)
    tasks = [
        sf.Task(
            cmd=f"Rscript --vanilla gen_results.R {i} {temp_dir}",
            outputs=f"{temp_dir}/res_{i}.RData",
            name=f"sim-{i}",
            setup=["export R_LIBS_USER=$HOME/R/library"],
        ).set_retry(2)
        for i in range(5)
    ]
    await sf.bag(*tasks)

    # Phase 2: Aggregate results (sequential)
    await sf.Task(
        cmd=f"Rscript --vanilla agg_results.R {temp_dir}",
        outputs="results.csv",
        name="agg-results"
    )