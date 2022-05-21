# creating the main executor!
maestro = None

def set_main_maestro(cr):
    global maestro
    maestro = cr

def get_main_maestro():
    return maestro


