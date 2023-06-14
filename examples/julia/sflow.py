"""
Example for scriptflow 

Adapt hpc parameters to your specifications. Then run via the command:
> scriptflow run mysim
"""

import scriptflow as sf
import os

# set executor to slurm or hpc 

# sf.init({ # Runner for Slurm
#     "executors":{
#         "slurm":{
#             "maxsize": 3,
#             "account": 'pi-chansen1',
#             "user": 'wiemann',
#             "partition": 'standard',
#             "modules": 'julia/1.8',
#             "walltime": '00:01:00'
#         } 
#     },
#     'debug': True
# })

# sf.init({ # Runner for PBS
#     "executors":{
#         "hpc":{
#             "maxsize": 3,
#             "modules": 'julia/1.8.3',
#             "walltime": '00:01:00'
#         } 
#     },
#     'debug': True
# })

sf.init({ # local runner
    "executors":{
        "local": {
            "maxsize" : 5
        } 
    },
    'debug':True
})

# create temp-directory to store results in
temp_dir = 'temp'
if not os.path.exists(temp_dir):
    os.mkdir(temp_dir)

# define a flow called Rit
async def flow_mysim():

    # Generates 5 simulation draws from a bivariate normal and stores as .csv
    tasks = [
        sf.Task(
        cmd = f"julia gen_results.jl {i}",
        outputs = f"{temp_dir}/res_{i}.csv",
        name = f"sim-{i}")
        for i in range(5)
    ]

    await sf.bag(*tasks)

    # Aggregates the simulation results and stores as .csv
    t_agg = sf.Task(
        cmd = f"julia agg_results.jl {temp_dir}",
        outputs = "results.csv",
        name = "agg-results")
    
    await t_agg

    # Can add cleanup-tasks here (e.g., to remove temp_dir)
    # for f in os.listdir(temp_dir):
    #     os.remove(os.path.join(temp_dir, f))