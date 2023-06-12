"""
Example for scriptflow on a slurm-hpc.

Adapt hpc parameters to your specifications. Then run via the command:
> scriptflow run Rit
"""

import scriptflow as sf
import os

# set executor to slurm or hpc 

sf.init({
    "executors":{
        "slurm":{
            "maxsize": 2,
            "account": 'pi-chansen1',
            "user": 'wiemann',
            "partition": 'standard',
            "modules": 'R/3.6/3.6.2',
            "walltime": '00:01:00'
        } 
    },
    'debug': True,
    'notify': "thomas"
})

# sf.init({
#     "executors":{
#         "hpc":{
#             "maxsize": 3,
#             "modules": 'R/3.5.3',
#             "walltime": '00:01:00'
#         } 
#     },
#     'debug': True,
#     'notify': "thomas"
# })

# create temp-directory to store results in
temp_dir = 'temp'
if not os.path.exists(temp_dir):
    os.mkdir(temp_dir)

# define a flow called Rit
async def flow_Rit():

    # Generates 5 simulation draws from a bivariate normal and stores as .csv
    tasks = [
        sf.Task(
        cmd = f"R --vanilla  '--args {i} {temp_dir}' < gen_results.R",
        outputs = f"{temp_dir}/res_{i}.RData",
        name = f"sim-{i}").set_retry(2)
        for i in range(5)
    ]

    await sf.bag(*tasks)

    # Aggregates the simulation results and stores as .csv
    t_agg = sf.Task(
        cmd = f"R --vanilla  '--args {temp_dir}' < agg_results.R",
        outputs = "results.csv",
        name = "agg-results")
    
    await t_agg

    # Can add cleanup-tasks here (e.g., to remove temp_dir)