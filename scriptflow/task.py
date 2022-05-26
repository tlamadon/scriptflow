
import hashlib
import asyncio
import shlex

from .glob import get_main_controller


"""

note: 
 - use input and output for dependencies and generated stuff
 - use set_args and set_return for input parameters and return values that should be collected

"""
class Task:
    cmd =""
    uid=""
    mem="32Gb"
    ncore="10"
    retry=0

    """
    Allows to construct the class with several options!
    """
    def __init__(self, **kwargs):

        self.fut = None
        self.state = "init"

        if "cmd" in kwargs.keys():
            # we check if we have a string, in which case we try to split it
            if isinstance(kwargs["cmd"], str):
                self.cmd = shlex.split(kwargs["cmd"])
            else:
                self.cmd  = kwargs["cmd"]

        if "controller" in kwargs.keys():
            self.controller  = kwargs["controller"]
        else:
            self.controller = get_main_controller()

        if "outputs" in kwargs.keys():
            self.output_file  = kwargs["outputs"]
        else:
            self.output_file = ""

        if "inputs" in kwargs.keys():
            self.deps  = kwargs["inputs"]
            if isinstance(kwargs["inputs"], str):
                self.deps = [kwargs["inputs"]]
            else:
                self.deps  = kwargs["inputs"]
        else:
            self.deps=[]

        if "name" in kwargs.keys():
            self.uid  = kwargs["name"]
        else:
            self.uid = ""

        self.quiet = True
        self.hash = ""
        self.return_file = ""
        self.props = {}

    def __await__(self):
        # we need to check if the task has been scheduled
        if not self.is_scheduled():
            self.schedule()

        return self.fut.__await__()

    def schedule(self):
        self.fut = asyncio.get_event_loop().create_future()
        self.state = "scheduled"
        self.hash = hashlib.md5("".join(self.get_command()).encode()).hexdigest()
        self.controller.add(self)
        return(self)

    def result(self, return_file):
        self.return_file = return_file    
        return self  

    def output(self, output_file):
        self.output_file = output_file    
        return self     

    def retry(self, retry):
        self.retry = retry    
        return self     

    def input(self, input_file):
        # check that the input file actually exist and extract its values, too soon?
        self.input_file = input_file  
        return self  

    def add_deps(self, deps):

        if isinstance(deps, str):
            self.deps.append(deps)
            return(self)

        if not hasattr(deps, "__iter__"): #returns True if type of iterable - same problem with strings
            deps = list(deps)
        
        for dep in deps:
            self.deps.append(dep)

        return(self)

    def name(self,name):
        self.name = name

    def uid(self,uid):
        self.uid = uid
        return self

    def get_command(self):
        return self.cmd

    def set_prop(self,name,value):
        self.props[name]=value

    def get_prop(self,name):
        return(self.props[name])

    def get_outputs(self):
        if self.output_file=="":
            return([])

        return([self.output_file])

    def set_state_scheduled(self):
        self.state = "scheduled"

    def is_scheduled(self):
        return(self.state == "scheduled")

    def set_state_completed(self):
        self.state = "completed"

    def set_completed(self):
        self.fut.set_result(self)
