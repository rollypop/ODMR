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
    - [x] code cannot find thorlabs_tsi_camera_sdk.dll file.
       * The frequently appearing error message is shown in path_error
    - [x] Find appropriate method of VNA to verify SynthHD sweep MW frequency for 10MHz per seconds.
    - [x] Check producer consumer structure.
    - [x] Do test of [magnetic_img.py](https://github.com/rollypop/ODMR/blob/main/magnetic_img.py) .
    - [ ] Fabricate MW antenna.

[CW_ODMR2.py](https://github.com/rollypop/ODMR/blob/main/CW_ODMR2.py) can convert trigger number to corresponding MW frequency appropriately.

[CW_ODMR3.py](https://github.com/rollypop/ODMR/blob/main/CW_ODMR3.py) can obtain data from selected ROI.

## 2. Magnetic image
[magnetic_img2.py](https://github.com/rollypop/ODMR/blob/main/magnetic_img2.py) is the code which can obtain magnetic image from vdW heterostructure(hBN + other vdW matter). But we need to check the code with real sample

After fitting MW sweep result to double dip Lorentzian function for every pixel in ROI, code find two resonance frequencies, $f_{\pm}$, and calculate $B_z=\frac{f_+-f_z}{2\gamma_e}$. Finally we can make map of $\Delta B_{z} = B_z - B_0$, where $B_0\simeq10mT$
