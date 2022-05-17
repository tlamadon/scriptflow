from omegaconf import OmegaConf
from .core import Controller
import logging
import asyncio

# creating the main executor!
maestro = None

def set_main_maestro(cr):
    global maestro
    maestro = cr

def get_main_maestro():
    return maestro

def init(dict):    
    conf = OmegaConf.create(dict)

    logging.basicConfig(filename='scriptflow.log', level=logging.DEBUG)

    set_main_maestro(Controller(conf))
    if conf.debug:
        asyncio.get_event_loop().set_debug(True)
