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
       * The frequently appearing error message is shown in patherror
    - [ ] Find appropriate method of VNA to verify SynthHD sweep MW frequency for 10MHz per seconds.
    - [ ] Check producer consumer structure.
