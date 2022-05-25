# scriptflow

[![CircleCI](https://circleci.com/gh/tlamadon/scriptflow/tree/main.svg?style=svg)](https://circleci.com/gh/tlamadon/scriptflow/tree/main) [![PyPI version](https://badge.fury.io/py/scriptflow.svg)](https://badge.fury.io/py/scriptflow) [![codecov](https://codecov.io/gh/tlamadon/scriptflow/branch/main/graph/badge.svg?token=0E8J7635HD)](https://codecov.io/gh/tlamadon/scriptflow)

Small library that allows scheduling scripts asyncrhonously on different platforms. Think of it as a Make when you can write the dependencies as python code, and that can run locally, on an HPC or in the cloud (cloud is not implemented just yet).

The status is very experimental. I will likely be changing the interface as I go. 

## Goals:

 - [x] works on windows / osx / linux
 - [x] describe dependencies as python code (using await/async)
 - [x] describe scripts with input/output as code
 - [x] clean terminal feedback (using rich)
 - [x] task retry
 - [x] check that output was generated 
 - [x] notifications (using light webserver at [scriptflow.lamadon.com](http://scriptflow.lamadon.com/) )
 - [x] send status to central web service
 - [x] resume flows
 - [ ] clean output
 - [ ] named runs
 - [x] store run information
 - [x] output diagnostic / reporting (tracing how files were created)
 - [x] simpler interface with default head executor and awaitable tasks
 - [x] skip computation based on timestamp of inputs and outpus
 - [ ] load and store tasks results
 - [ ] remove existing output of task if task is started (issue with failing tasks that look like they worked)
 - executors :
   - [x] local excutor using subprocess 
   - [x] HPC excutor (monitoring qsub) 
   - [ ] docker Executor 
   - [ ] aws executor (probably using Ray)
   - [ ] dask executor  
 - [x] add check on qsub return values
 - [x] select flow by name from terminal 
 - [ ] ? scripts can create tasks, not sure how to await them. 
 - reporting:
   - [ ] input and output hashes
   - [x] start and end datetimes
 - notification system
   - [x] allow to send messages
   - [ ] allow for runs
   - [ ] allow to send messages with html content like images
 - writing tasks and flows 
   - [ ] cache flows in addition to caching tasks (avoid same task getting scheduled from 2 places)
   - [ ] a functional api for task creation with hooks
   - [ ] a functional api for flows
   - [ ] controller could parse the log file for results (looking for specific triggers)
   - [ ] allow for glob output/input
   - [ ] provide simple toml/json interface for simple tasks and flows
   - [x] use `shlex` to parse command from strings
 - cli
   - [ ] pass arguments to flows 
   - [ ] create portable executable


## Simple flow example:

Create a file `sflow.py` with:

```python
import scriptflow as sf

# set main options
sf.init({
    "executors":{
        "local": {
            "maxsize" : 5
        } 
    },
    'debug':True
})

# example of a simple step that combines outcomes
def step2_combine_file():
    with open('test_1.txt') as f:
        a = int(f.readlines()[0])
    with open('test_2.txt') as f:
        b = int(f.readlines()[0])
    with open('final.txt','w') as f:
        f.write("{}\n".format(a+b))

# define a flow called sleepit
async def flow_sleepit():

    i=1
    t1 = sf.Task(
      cmd    = f"""python -c "import time; time.sleep(5); open('test_{i}.txt','w').write('5');" """,
      output = f"test_{i}.txt",
      name   = f"solve-{i}")

    i=2
    t1 = sf.Task(
      cmd    = f"""python -c "import time; time.sleep(5); open('test_{i}.txt','w').write('5');" """,
      output = f"test_{i}.txt",
      name   = f"solve-{i}")

    await sf.bag(t1,t2)

    tfinal = sf.Task("python -c 'import sflow; sflow.step2_combine_file()'")
    tfinal.output(f"final.txt").uid(f"final").add_deps([t1.output_file,t2.output_file])
    await tfinal
```        

then create a local env, activate, install and run!

```shell
python3 -m venv env
source env/bin/activate
pip install scriptflow
scritpflow run sleepit
```

## Life cycle of a task

1. the task object is created. All properties can be edited.
2. the task is sent to an executor. At this point, the properties of the task are frozen. They can be read, copied but not changed. A unique ID id created from the task from its command and its inputs. The task can be sent by using the `start()` method, or it will be sent automatically when awaited.
3. the task is awaited, and hence execution is blocked until the task is finished. Nothing can be done at that stage. Again, the task is automatically sent at this stage if it has not be done before. Also note that several tasks can be awaited in parallel by bagging them with `sf.bag(...)`.
4. the task is completed, the await returns. The task has now it's output attached to it, it can be used in the creation of other tasks.

## Inspiration / Alternatives

I have tried to use the following three alternatives which are all truly excelent!

 - [pydoit](https://pydoit.org/)
 - [nextflow](https://www.nextflow.io/)
 - [snakemake](https://snakemake.readthedocs.io/en/stable/)

There were use cases that I could not implement cleanly in the dataflow model of nextflow. I didn't like that snakemake relied on file names to trigger rules, I was constently juggling complicated file names. Pydoit is really great, but I couldn't find how to extend it to build my own executor, and I always found myself confused writing new tasks and dealing with dependencies. 

## Developing

the package is managed using poetry, install poetry first then 

```
poetry install

# run example
cd examples/simple-local
poetry run scriptflow run sleepit

# run tests with coverate
poetry run python -m pytest --cov=scriptflow
poetry run coverage xml
poetry run codecov -t <token>

```




### Docker images to try the different schedulers

 - [PBS](https://openpbs.atlassian.net/wiki/spaces/PBSPro/pages/79298561/Using+Docker+to+Instantiate+PBS)
 - [slurm](https://medium.com/analytics-vidhya/slurm-cluster-with-docker-9f242deee601)
=======
