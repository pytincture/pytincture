def wrapper_function(wrapdict):
    #go to server to run me
    print("whois",whois)

class Window:
   def __init__(self):
       pass

   def __getattr__(self, name):
       return wrapper_function
       
def client_side(self, func):
    try:
        func()
    except NameError as err:
        wrapper_function()
    		

class TinctureClass:
   def __init__(self, class_object):
       self.mw = class_object()

   def __getattr__(self, name):
       if name in dir(self.mw):
           return getattr(self.mw, name)
       else:
           return wrapper_function