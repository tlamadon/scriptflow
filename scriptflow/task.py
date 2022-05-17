
import hashlib
import asyncio

from .glob import get_main_maestro


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

    def __init__(self, cmd):
        self.fut = asyncio.get_event_loop().create_future()
        self.running = False
        self.task = None
        self.cmd = cmd
        self.deps = []
        self.output_file = ""
        self.quiet = True
        self.hash = ""
        self.return_file = ""
        self.props = {}

    def __await__(self):
        if self.task is None:
            self.start()
        return self.fut.__await__()

    def start(self):
        self.running = True
        self.hash = hashlib.md5("".join(self.get_command()).encode()).hexdigest()

        get_main_maestro().add(self)
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