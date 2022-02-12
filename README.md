# scriptflow

Small library that allows scheduling scripts asyncrhonously on different platforms. Think of it as a Make when you can write the dependencies as python code, and that can run locally, on an HPC or in the cloud (cloud is not implemented just yet).

The status is very experimental. I will likely be changing the interface as I go. 

## Goals:

 - [x] works on windows / osx / linux
 - [x] describe dependencies as python code (using await/async)
 - [x] describe scripts with input/output as code
 - [x] clean terminal feedback (using rich)
 - [x] task retry
 - [x] check that output was generated 
 - [ ] notifications
 - [x] send status to central web service
 - [x] resume flows
 - [ ] clean output
 - [ ] named runs
 - [ ] store run information
 - [ ] output diagnostic / reporting (tracing how files were created)
 - [x] simpler interface with default head executor and awaitable tasks
 - [x] skip computation best on timestamp of inputs and outpus
 - [ ] load and store tasks results
 - executors :
   - [x] local excutor using subprocess 
   - [x] HPC excutor (monitoring qsub) 
   - [ ] docker Executor 
   - [ ] aws executor (probably using Ray)
   - [ ] dask executor  
 - [ ] cache flows in addition to caching tasks (avoid same task getting scheduled from 2 places)
 - [x] add check on qsub return values
 - [x] select flow by name from terminal 
 - [ ] allow for glob output/input

## Simple flow example:


```python
from scriptflow import Task

async def flow_sleepit():

    # create tasks
    tasks = [
      Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
                    .result(f"res{i}.txt")
                    .uid(f"solve-{i}")))
      for i in range(5)]
      
    # await then in parelell
    await asyncio.gather(*tasks)
```                    

## Inspiration / Alternatives

I have tried to use the following three alternatives which are all truly excelent!

 - [pydoit](https://pydoit.org/)
 - [nextflow](https://www.nextflow.io/)
 - [snakemake](https://snakemake.readthedocs.io/en/stable/)

There were use cases that I could not implement cleanly in the dataflow model of nextflow. I didn't like that snakemake relied on file names to trigger rules, I was constently juggling complicated file names. Pydoit is really great, but I couldn't find how to extend it to build my own executor, and I always found myself confused writing new tasks and dealing with dependencies. 
