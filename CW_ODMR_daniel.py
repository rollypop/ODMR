import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
import datetime
import glob
import os
import pyvisa
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pandas as pd  
from datetime import date


try:
    # if on Windows, use the provided setup script to add the DLLs folder to the PATH
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None


n_frames = 20000  
mw_start = 3e9   
mw_step = 5e7    
mw_steps = 20     

roi_x_start = 550
roi_x_end = 1001

roi_y_start = 400
roi_y_end = 801
 

frame_queue = queue.Queue(maxsize=n_frames)
intensity_dict = {} 


frames_captured = 0
capture_lock = threading.Lock()
camera_ready = False 
measurement_complete = False

# -----------------------------------------
# SDG2082x control functions (for PyVISA)
# -----------------------------------------
def sdg_control():
    rm = pyvisa.ResourceManager()
    try:
        sdg = rm.open_resource("USB0::0xF4EC::0xEE38::SDG2XCAD1R2393::INSTR")
    except Exception as e:
        print("SDG2082x connection failure:", e)
        return

    try:
        sdg.write("*RST")
        time.sleep(0.1)
        sdg.write("C1:BSWV WVTP,PULSE")   
        sdg.write("C1:BSWV FRQ,25")        
        sdg.write("C1:BSWV AMP,2")
        sdg.write("C1:BSWV OFST,1")
        sdg.write("C1:BSWV WIDTH,2e-4")     
    except Exception as e:
        print("Error in SDG initial settings:", e)
        return

    print("Starting SDG2082x operation : camera standby...")
    while not camera_ready:
        time.sleep(0.01)
    print("Camera setup. SDG2082x start pulsing")
    sdg.write("C1:OUTP ON")  

    global frames_captured
    while True:
        with capture_lock:
            if frames_captured >= n_frames:
                break
        time.sleep(0.01)
    sdg.close()
    print(f"SDG operation complete: {n_frames} collected and SDG turned off")

# -----------------------------------------
# Camera Producer function
# -----------------------------------------
def camera_producer(exposure_time = 20000, frames_per_trigger_zero_for_unlimited=1, image_poll_timeout_ms = 1000,frame_rate_control_value=25,is_frame_rate_control_enabled=True):
    global frames_captured, camera_ready
    with TLCameraSDK() as sdk:
        available_cameras = sdk.discover_available_cameras()
        if len(available_cameras) < 1:
            print("Camera not detected.")
            return
        with sdk.open_camera(available_cameras[0]) as camera:
            camera.exposure_time_us = exposure_time  # 20 ms exposure
            camera.frames_per_trigger_zero_for_unlimited = frames_per_trigger_zero_for_unlimited
            camera.image_poll_timeout_ms = image_poll_timeout_ms
            camera.frame_rate_control_value = frame_rate_control_value
            camera.is_frame_rate_control_enabled = is_frame_rate_control_enabled

            camera.operation_mode = OPERATION_MODE.HARDWARE_TRIGGERED
            camera.arm(2)
            print("Camera ARM: hardware trigger on standby")
            camera_ready = True  # Camera ready

            try:
                while True:
                    with capture_lock:
                        if frames_captured >= n_frames:
                            break
                    frame = camera.get_pending_frame_or_null()
                    if frame is not None:
                        image_buffer_copy = np.copy(frame.image_buffer)
                        img = image_buffer_copy.reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        frame_queue.put((frame.frame_count, img))
                        with capture_lock:
                            frames_captured += 1
                        # For debugging
                        if frames_captured % 1000 == 0:
                            print(f"DEBUG: {frames_captured} frames collected")
                        # Total number of frames collecteds
                        print(f"Frame #{frame.frame_count} Saved (Total {frames_captured}/{n_frames})")
            except KeyboardInterrupt:
                print("Camera producer exit (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# Camera Consumer function (ROI processing: Added intensity sum and debugging logs)
# -----------------------------------------
def camera_consumer(file_path,roi_x_start, roi_x_end, roi_y_start, roi_y_end,ignore_frame = 18):
    global measurement_complete, intensity_dict, df
    intensity_dict = {}
    processed_frames = 0

    df = pd.DataFrame()
    columns = ["Frequency (Hz)", "Intensity"]
    today = date.today()

    file_name = file_path + f"CW_ODMR({today}).csv"
    df.to_csv(file_name,columns=columns,index=False)
    
    # ROI: x: 550~1000, y: 400~800 (Python index: y 400:801, x 550:1001)    
   
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            if frame_num < ignore_frame: # Collected after 18th frame
                continue
            # Extract ROI
            roi = image[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            roi_total_intensity = np.sum(roi)
            # MW frequency calculation: (frame_num - 1) % mw_steps (Hz)
            step_index = (frame_num - 1) % mw_steps
            freq = mw_start + step_index * mw_step  # Hz
            df = pd.read_csv(file_name)
            if freq in intensity_dict:
                intensity_dict[freq].append(roi_total_intensity)
                df.loc[df['Frequency (Hz)'] == freq, "Intensity"] = np.mean(roi_total_intensity)
               
            else:
                intensity_dict[freq] = [roi_total_intensity]
                row = pd.DataFrame([{"Frequency (Hz)": freq, "Intensity": roi_total_intensity}])
                row.to_csv(file_name,mode="a",header=False,index=False)
            processed_frames += 1
            # Debugging: check data point every 500 frames
            if processed_frames % 500 == 0:
                print(f"DEBUG: consumed frames {processed_frames}, present intensity_dict entries: {len(intensity_dict)}")
            print(f"Frame #{frame_num} processed: MW freq = {freq/1e9:.3f} GHz, ROI total intensity = {roi_total_intensity}")
        except queue.Empty:
            continue
    measurement_complete = True

# -----------------------------------------
# Thread start and data collection
# -----------------------------------------

if __name__ == "__main__":
    file_path = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
    sdg_thread = threading.Thread(target=sdg_control, daemon=True)
    producer_thread = threading.Thread(target=camera_producer,args= (), daemon=True)
    consumer_thread = threading.Thread(target=camera_consumer, args = (file_path,roi_x_start, roi_x_end, roi_y_start, roi_y_end), daemon=True)


    producer_thread.start()
    consumer_thread.start()
    sdg_thread.start()

    producer_thread.join()
    consumer_thread.join()
    sdg_thread.join()

    # Debugging: check intensity_dict entry numbers
    for freq in sorted(intensity_dict.keys()):
        print(f"DEBUG: Number of entries measure at frequency {freq/1e9:.3f} GHz: {len(intensity_dict[freq])}")



