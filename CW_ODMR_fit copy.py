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
from scipy.optimize import curve_fit

# number of initial frames to discard before processing
INITIAL_FRAMES_TO_DISCARD = 18


try:
    # if on Windows, use the provided setup script to add the DLLs folder to the PATH
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None


n_frames = 20000
# Load frequency list for labeling (middle column of CSV in MHz)
sweep_table_path = 'cw_sweeptable.csv'
# CSV has no header: columns are [step_index, freq_MHz, power_dBm]
try:
    sweep_df = pd.read_csv(sweep_table_path, header=None)
    sweep_freqs = sweep_df.iloc[:, 1].values * 1e6  # Hz
    num_steps = len(sweep_freqs)
except FileNotFoundError:
    print(f"Error: Sweep table file not found: {sweep_table_path}")
    raise
except Exception as e:
    print(f"Error reading sweep table: {e}")
    raise


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
                        # Log every 1000 frames only
                        if frames_captured % 1000 == 0:
                            print(f"DEBUG: {frames_captured} frames collected (Frame count {frame.frame_count})")
            except KeyboardInterrupt:
                print("Camera producer exit (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# Camera Consumer function (ROI processing: Added intensity sum and debugging logs)
# -----------------------------------------
def camera_consumer(file_path, roi_x_start, roi_x_end, roi_y_start, roi_y_end):
    global intensity_dict
    today = date.today()
    file_name = os.path.join(file_path, f"CW_ODMR({today}).csv")
    intensity_dict = {}
    processed_frames = 0

    # Prepare cumulative CSV DataFrame
    freqs_ghz = np.array(sorted(sweep_freqs)) / 1e9
    df = pd.DataFrame({'MW freq (GHz)': freqs_ghz})
    df.set_index('MW freq (GHz)', inplace=True)

    # ROI: x: 550~1000, y: 400~800 (Python index: y 400:801, x 550:1001)    
   
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            if frame_num < INITIAL_FRAMES_TO_DISCARD: # Collected after initial discard frames
                continue
            # Extract ROI
            roi = image[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            roi_total_intensity = np.sum(roi)
            step_index = (frame_num - INITIAL_FRAMES_TO_DISCARD) % num_steps
            freq = sweep_freqs[step_index]      # Hz
            # accumulate in memory
            intensity_dict.setdefault(freq, []).append(roi_total_intensity)
            processed_frames += 1
            # Update cumulative DataFrame
            # Find row corresponding to this freq
            freq_ghz = freq / 1e9
            values = intensity_dict[freq]  # list of all intensities for this freq
            # Ensure enough PL columns
            for i, val in enumerate(values, start=1):
                col = f'PL {i}'
                df.loc[freq_ghz, col] = val
            # Update average column
            df.loc[freq_ghz, 'Avg. PL'] = np.mean(values)
            # Save to CSV after this update
            df.reset_index().to_csv(file_name, index=False)
            # Debugging: check data point every 500 frames
            if processed_frames % 500 == 0:
                print(f"DEBUG: consumed frames {processed_frames}, present intensity_dict entries: {len(intensity_dict)}")
            print(f"Frame #{frame_num} processed: MW freq = {freq/1e9:.3f} GHz, ROI total intensity = {roi_total_intensity}")
        except queue.Empty:
            continue

    global measurement_complete
    measurement_complete = True

# -----------------------------------------
# Perform summary ODMR fit and plot
# -----------------------------------------
def fit_summary_odmr(intensity_dict):
    """
    Perform dynamic initial-guess Lorentzian fitting
    on the summary CW-ODMR spectrum stored in intensity_dict.
    Returns fitted frequencies f1, f2 in GHz.
    """
    # Build frequency and intensity arrays (GHz), using exact Hz keys to avoid float mismatch
    hz_keys = sorted(intensity_dict.keys())
    freqs = np.array(hz_keys) / 1e9
    intensities = np.array([np.mean(intensity_dict[k]) for k in hz_keys])

    # dynamic initial guess for summary fit
    split = (freqs[0] + freqs[-1]) / 2
    mask_low  = freqs < split
    mask_high = freqs >= split
    idx_low   = np.argmin(intensities[mask_low])
    idx_high  = np.argmin(intensities[mask_high])
    f1_guess  = freqs[mask_low][idx_low]
    f2_guess  = freqs[mask_high][idx_high]
    A1_guess  = np.max(intensities) - intensities[mask_low][idx_low]
    A2_guess  = np.max(intensities) - intensities[mask_high][idx_high]
    g1_guess  = 0.01
    g2_guess  = 0.01
    C_guess   = np.median(intensities)
    p0_summary = [A1_guess, f1_guess, g1_guess,
                  A2_guess, f2_guess, g2_guess,
                  C_guess]

    # Bounds for fitting
    lower = [0, 3.0, 0.001, 0, 3.5, 0.001, np.min(intensities)]
    upper = [np.ptp(intensities), 3.4, 0.1,
             np.ptp(intensities), 4.0, 0.1,
             np.max(intensities)]

    # Define the double Lorentzian model
    def double_lorentzian_ghz(f, A1, f1, g1, A2, f2, g2, C):
        lor1 = A1 * g1**2 / ((f - f1)**2 + g1**2)
        lor2 = A2 * g2**2 / ((f - f2)**2 + g2**2)
        return C - (lor1 + lor2)

    # Perform fit
    popt, _ = curve_fit(
        double_lorentzian_ghz,
        freqs, intensities,
        p0=p0_summary,
        bounds=(lower, upper),
        maxfev=10000
    )
    f1, f2 = popt[1], popt[4]

    # Plot result
    plt.figure()
    plt.plot(freqs, intensities, 'bo', label='Data')
    plt.plot(freqs, double_lorentzian_ghz(freqs, *popt),
             'r-', label=f'Fit: f1={f1:.3f} GHz, f2={f2:.3f} GHz')
    plt.xlabel('Frequency (GHz)')
    plt.ylabel('ROI Intensity')
    plt.title('CW-ODMR Spectrum and Fit')
    plt.legend()
    plt.show()

    return f1, f2


# -----------------------------------------
# Thread start and data collection
# -----------------------------------------

if __name__ == "__main__":
    file_path = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
    # Validate data directory
    if not os.path.isdir(file_path):
        raise NotADirectoryError(f"Data directory does not exist: {file_path}")

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

    # Perform summary fit
    f1_s, f2_s = fit_summary_odmr(intensity_dict)
    print(f"Resonances: f₋={f1_s:.3f} GHz, f₊={f2_s:.3f} GHz")
