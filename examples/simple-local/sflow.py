"""
Simple example with dependencis. Install scriptflow with pip install scriptflow then run 

> scriptflow run sleepit

"""


import scriptflow as sf

# set main options
sf.init({
    "executors":{
        "local": {
            "maxsize" : 5
        } 
    },
    'debug':True,
    'classic_terminal':False
})


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
    t1 = sf.Task(
        cmd = f"""python -c "import time; time.sleep(2); open('test_{i}.txt','w').write('5');" """,
        outputs = f"test_{i}.txt",
        name = f"solve-{i}")

    i=2
    t2 = sf.Task(
        cmd = f"""python -c "import time; time.sleep(2); open('test_{i}.txt','w').write('5');" """,
        outputs = f"test_{i}.txt",
        name = f"solve-{i}")

    await sf.bag(t1,t2)

    tfinal = sf.Task(
        cmd = f"""python -c "import sflow; sflow.compare_file()" """,
        outputs = "final.txt",
        name = "final",
        inputs = [*t1.get_outputs(),*t1.get_outputs()])

    await tfinal

    # tasks = [ sf.Task(
    #     ["python", "-c", f"import time; time.sleep(5); open('test_{i}.txt','w').write('4');"]).uid(f"test_{i}").output(f"test_{i}.txt") for i in range(10,20)]
    # await sf.bag(*tasks)





