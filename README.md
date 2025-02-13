# ODMR
Python code to manipulate Thorlabs Zelux CMOS camera and Siglent sdg2082x(& Windfreak SynthHD) for hBN **CW_ODMR** and **Pulsed_ODMR** experiments

## 1. CW_ODMR
1. Apparatus
    1. Laser : Newport LQC658-30C (658nm CW laser)
    2. AWG : siglent sdg2082x (use only ch1)   

2. Method
    1. Make 1Hz pulse signal (amp:2.0Vpp & offset:1.0V) and send it to camera and SynthHD simulatenously
    2. Camera is set to Hardware triggered mode and triggered by pulse. Then camera obtain image frame **for every seconds**.
    3. SynthHD is also set to Hardware triggerd mode. For every input trigger SynthHD make MW frequency sweep. The sweep step is 10MHz and the range of frequency sweep is 3GHz ~ 4GHz.

3. Trouble shooting
    - [ ] code cannot find thorlabs_tsi_camera_sdk.dll file.
       * The frequently appearing error message is 
        '''
        PS C:\Users\user\Documents\hBN magnetrometry\Camera_Examples> & C:/Users/user/AppData/Local/Microsoft/WindowsApps/python3.13.exe "c:/Users/user/Documents/hBN magnetrometry/Camera_Examples/Python/CW_ODMR.py"
Exception in thread Thread-2 (camera_producer):
Traceback (most recent call last):
  File "C:\Users\user\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\thorlabs_tsi_sdk\tl_camera.py", line 178, in __init__
    self._sdk = cdll.LoadLibrary(r"thorlabs_tsi_camera_sdk.dll")
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.752.0_x64__qbz5n2kfra8p0\Lib\ctypes\__init__.py", line 471, in LoadLibrary
    return self._dlltype(name)
           ~~~~~~~~~~~~~^^^^^^
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.752.0_x64__qbz5n2kfra8p0\Lib\ctypes\__init__.py", line 390, in __init__
    self._handle = _dlopen(self._name, mode)
                   ~~~~~~~^^^^^^^^^^^^^^^^^^
FileNotFoundError: Could not find module 'thorlabs_tsi_camera_sdk.dll' (or one of its dependencies). Try using the full path with constructor syntax.

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.752.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 1041, in _bootstrap_inner
    self.run()
    ~~~~~~~~^^
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.752.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 992, in run
    self._target(*self._args, **self._kwargs)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "c:\Users\user\Documents\hBN magnetrometry\Camera_Examples\Python\CW_ODMR.py", line 68, in camera_producer   
    with TLCameraSDK() as sdk:
         ~~~~~~~~~~~^^
  File "C:\Users\user\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\thorlabs_tsi_sdk\tl_camera.py", line 188, in __init__
    raise TLCameraError(str(os_error) +
    ...<4 lines>...
                        "and 64-bit libraries when using a 64-bit interpreter.\n")
thorlabs_tsi_sdk.tl_camera.TLCameraError: Could not find module 'thorlabs_tsi_camera_sdk.dll' (or one of its dependencies). Try using the full path with constructor syntax.
Unable to load library - are the thorlabs tsi camera sdk libraries discoverable from the application directory? Try placing them in the same directory as your program, or adding the directory with the libraries to the PATH. Make sure to use 32-bit libraries when using a 32-bit python interpreter and 64-bit libraries when using a 64-bit interpreter.
        '''
    - [ ] Find appropriate method of VNA to verify SynthHD sweep MW frequency for 10MHz per seconds.
    - [ ] Check producer consumer structure.
