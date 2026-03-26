"""
Example: Running Python tasks locally.

This example uses the local executor (subprocess). Run via:
> scriptflow run sleepit
"""

import scriptflow as sf

# ==============================================================================
# Choose ONE executor below, and adapt parameters to your cluster.
# ==============================================================================

# --- Option 1: Local (default for this example) ---
sf.init({
    "executors": {
        "local": {"maxsize": 5}
    },
    "debug": True
})

# --- Option 2: Slurm ---
# sf.init({
#     "executors": {
#         "slurm": {
#             "maxsize": 5,
#             "account": "my-account",
#             "user": "myuser",
#             "partition": "standard",
#             "modules": "python/3.12",
#             "walltime": "00:05:00"
#         }
#     },
#     "debug": True
# })

# --- Option 3: PBS ---
# sf.init({
#     "executors": {
#         "hpc": {
#             "maxsize": 5,
#             "user": "myuser",
#             "modules": "python/3.12",
#             "walltime": "00:05:00"
#         }
#     },
#     "debug": True
# })

# ==============================================================================
# Flow
# ==============================================================================

def compare_file():
    with open("test_1.txt") as f:
        a = int(f.readlines()[0])
    with open("test_2.txt") as f:
        b = int(f.readlines()[0])
    with open("final.txt", "w") as f:
        f.write(f"{a + b}\n")


async def flow_sleepit():
    """Create two files in parallel, then combine them."""

    # Phase 1: Generate two files (parallel)
    t1 = sf.Task(
        cmd="""python -c "import time; time.sleep(2); open('test_1.txt','w').write('5')" """,
        outputs="test_1.txt",
        name="solve-1"
    )
    t2 = sf.Task(
        cmd="""python -c "import time; time.sleep(2); open('test_2.txt','w').write('5')" """,
        outputs="test_2.txt",
        name="solve-2"
    )
    await sf.bag(t1, t2)

    # Phase 2: Combine results (sequential)
    await sf.Task(
        cmd="""python -c "import sflow; sflow.compare_file()" """,
        outputs="final.txt",
        name="final"
    )
