"""
Example: Running Julia simulations on an HPC cluster.

Uncomment the executor block for your cluster's scheduler, then run:
> scriptflow run mysim
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
            "maxsize": 3,
            "account": "my-account",   # your Slurm account
            "user": "myuser",          # your cluster username
            "partition": "standard",
            "modules": "julia/1.10",
            "walltime": "00:05:00"
        }
    },
    "debug": True
})

# --- Option 2: PBS ---
# sf.init({
#     "executors": {
#         "hpc": {
#             "maxsize": 3,
#             "user": "myuser",
#             "modules": "julia/1.10",
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

async def flow_mysim():
    """Generate 5 simulation draws, then aggregate."""

    # Phase 1: Generate simulation draws (parallel)
    tasks = [
        sf.Task(
            cmd=f"julia gen_results.jl {i}",
            outputs=f"{temp_dir}/res_{i}.csv",
            name=f"sim-{i}"
        ).set_retry(2)
        for i in range(5)
    ]
    await sf.bag(*tasks)

    # Phase 2: Aggregate results (sequential)
    await sf.Task(
        cmd=f"julia agg_results.jl {temp_dir}",
        outputs="results.csv",
        name="agg-results"
    )