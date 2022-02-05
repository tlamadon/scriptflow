# scriptflow

Small library that allows scheduling scripts asyncrhonously on different platforms. Think of it as a Make when you can write the dependencies as python code, and that can run locally, on an HPC or in the cloud (cloud is not implemented just yet).

The status is very experimental. I will likely be changing the interface as I go. 

## Goal:

 - [x] works on windows / osx / linux
 - [x] describe dependencies as python code (using await/async)
 - [x] describe scripts with input/output as code
 - [x] clean terminal feedback (using rich)
 - [x] HPC executor
 - [x] task retry
 - [ ] notifications
 - [ ] send status to central web service
 - [ ] resume flows
 - [ ] named runs
 - [ ] store run information
 - [ ] simpler interface with default head executor and awaitable tasks
 - [ ] load and store tasks results

## Simple flow example:

```python
async def main():
    cr = sf.HpcRunner(3)    
    cr.log("starting loop")
    loop = asyncio.create_task(cr.loop())

    tasks = []
    for i in range(5):
        t =  cr.createTask( sf.Task("sleep 2; echo {} > res{}.txt".format("hi",i).split(" "))
                    .result(f"res{i}.txt")
                    .uid(f"solve-{i}")))
        tasks.append(t)
        
    await asyncio.gather(*tasks)
```                    

## Inspiration / Alternatives

I have tried to use the following three alternatives which are all truly excelent!

 - [pydoit](https://pydoit.org/)
 - [nextflow](https://www.nextflow.io/)
 - [snakemake](https://snakemake.readthedocs.io/en/stable/)

There were use cases that I could not implement cleanly in the dataflow model of nextflow. I didn't like that snakemake relied on file names to trigger rules, I was constently juggling complicated file names. Pydoit is really great, but I couldn't find how to extend it to build my own executor, and I always found myself confused writing new tasks and dealing with dependencies. 
