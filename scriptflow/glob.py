# creating the main executor!
global_controller = None

def set_main_controller(cr):
    global global_controller
    global_controller = cr

def get_main_controller():
    return global_controller


