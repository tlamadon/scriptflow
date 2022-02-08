"""
    Trying out a new awaitable structure for tasks, this might apply to flow as well
"""


import asyncio
from re import T

class Task:

    def __init__(self,name) -> None:
        self.fut = asyncio.get_event_loop().create_future()
        self.running = False
        self.task = None
        self.name = name
        pass

    def __await__(self):
        if self.task is None:
            self.start()
        return self.fut.__await__()

    def start(self):
        self.running = True
        self.task = asyncio.create_task(self.compute())
        return(self)

    async def compute(self):
        print("task {} started".format(self.name))
        await asyncio.sleep(1)
        print("task {} completed".format(self.name))
        self.fut.set_result(self)
        
async def mark_done(task):    
    await asyncio.sleep(1)
    task.done()

async def main():

    # create a task, then await it
    t = Task("1")
    await t

    # create a task, scedule it and await it
    t = Task("2").start()
    await t

    # await it again...
    await t

    # create 2 tasks, await them 
    t1 = Task("3.1")
    t2 = Task("3.2")
    await asyncio.gather(t1,t2)

    # create 2 tasks, start 1, await them 
    t1 = Task("4.1").start()
    t2 = Task("4.2")
    await asyncio.gather(t1,t2)


if __name__ == "__main__":
    asyncio.run(main())