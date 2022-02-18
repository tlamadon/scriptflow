#!/usr/bin/python3

import asyncio
import scriptflow as sf
import random

# set main maestro
cr = sf.CommandRunner(3)
sf.set_main_maestro(cr)

def compare_file():

    with open('test_1.txt') as f:
        a = int(f.readlines()[0])

    with open('test_2.txt') as f:
        b = int(f.readlines()[0])

    with open('final.txt','w') as f:
        f.write("{}\n".format(a+b))


# define a flow called sleepit
async def flow_sleepit():

    i=1
    t1 = sf.Task(["python", "-c", f"import time; time.sleep(5); open('test_{i}.txt','w').write('5');"])
    t1.output(f"test_{i}.txt").uid(f"solve-{i}")

    i=2
    t2 = sf.Task(["python", "-c", f"import time; time.sleep(5); open('test_{i}.txt','w').write('4');"])
    t2.output(f"test_{i}.txt").uid(f"solve-{i}")

    await sf.bag(t1,t2)

    tfinal = sf.Task(["python", "-c", "import sflow; sflow.compare_file()"])
    tfinal.output(f"final.txt").uid(f"final").add_deps([t1.output_file,t2.output_file])
    await tfinal




