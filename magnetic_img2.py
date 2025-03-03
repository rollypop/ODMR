import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import datetime
import glob
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pyvisa
import os

try:
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None

# 측정할 프레임 수
n_frames = 500

# MW sweep 파라미터
mw_start = 3e9    # 3 GHz
mw_step = 1e7     # 10 MHz
mw_steps = 100    # 100 steps (3 GHz ~ 4 GHz)

# 전역 데이터: Producer에서 수집한 이미지와 각 프레임의 MW 주파수를 저장할 리스트
image_list = []       # 각 프레임의 2D 이미지 (numpy array)
mw_frequencies = []   # 각 프레임에 해당하는 MW frequency (Hz)

# 동기화 변수
frames_captured = 0
capture_lock = threading.Lock()
camera_ready = False  # 카메라가 ARM되어 준비되었음을 표시

# -----------------------------------------
# SDG2082x 제어 함수 (PyVISA 이용)
# -----------------------------------------
def sdg_control():
    rm = pyvisa.ResourceManager()
    try:
        sdg = rm.open_resource("USB0::0xF4EC::0xEE38::SDG2XCAD1R2393::INSTR")
    except Exception as e:
        print("SDG2082x 연결 실패:", e)
        return

    try:
        sdg.write("*RST")
        time.sleep(0.1)
        sdg.write("C1:BSWV WVTP,PULSE")
        sdg.write("C1:BSWV FRQ,25")         # 25 Hz 펄스 → 40ms 주기
        sdg.write("C1:BSWV AMP,2")
        sdg.write("C1:BSWV OFST,1")
        sdg.write("C1:BSWV WIDTH,2e-4")
        sdg.write("C1:OUTP ON")
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    while not camera_ready:
        time.sleep(0.01)
    print("카메라 준비 완료. SDG2082x 펄스 출력 시작")
    
    global frames_captured
    while True:
        with capture_lock:
            if frames_captured >= n_frames:
                break
        time.sleep(0.01)
    sdg.close()
    print("SDG 제어 종료: 500프레임 수집 후 SDG 꺼짐")

# -----------------------------------------
# 카메라 Producer 함수
# -----------------------------------------
def camera_producer():
    global frames_captured, camera_ready
    with TLCameraSDK() as sdk:
        available_cameras = sdk.discover_available_cameras()
        if len(available_cameras) < 1:
            print("카메라가 감지되지 않았습니다.")
            return
        with sdk.open_camera(available_cameras[0]) as camera:
            camera.exposure_time_us = 20000  # 20 ms 노출
            camera.frames_per_trigger_zero_for_unlimited = 1
            camera.image_poll_timeout_ms = 1000
            camera.frame_rate_control_value = 25
            camera.is_frame_rate_control_enabled = True

            camera.operation_mode = OPERATION_MODE.HARDWARE_TRIGGERED
            camera.arm(2)
            print("카메라 ARM: 하드웨어 트리거 대기 중")
            camera_ready = True

            try:
                while True:
                    with capture_lock:
                        if frames_captured >= n_frames:
                            break
                    frame = camera.get_pending_frame_or_null()
                    if frame is not None:
                        img = np.copy(frame.image_buffer).reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        image_list.append(img)
                        current_frame = frame.frame_count
                        freq = mw_start + ((current_frame - 1) % mw_steps) * mw_step
                        mw_frequencies.append(freq)
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{current_frame} 저장 (총 {frames_captured}/{n_frames})")
            except KeyboardInterrupt:
                print("카메라 Producer 종료 (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# Magnetic Image Processing 함수 (ROI 적용)
# -----------------------------------------
def magnetic_image_processing():
    if len(image_list) == 0:
        print("수집된 이미지가 없습니다.")
        return
    cube = np.stack(image_list, axis=0)  # shape: (n_frames, H, W)
    n, H, W = cube.shape

    # ROI 영역 (예: x: 550~1000, y: 400~800)
    roi_y_start, roi_y_end = 400, 801
    roi_x_start, roi_x_end = 550, 1001
    roi_cube = cube[:, roi_y_start:roi_y_end, roi_x_start:roi_x_end]
    roi_H, roi_W = roi_cube.shape[1], roi_cube.shape[2]

    freqs = np.array(mw_frequencies)  # shape: (n_frames,)

    # 기본 파라미터 (Zeeman 계산용)
    gamma_e = 28e9  # 28 GHz/T
    D_ref = 3.48e9  # 기준 ODMR 중심 주파수, 3.48 GHz

    Bz_map = np.zeros((roi_H, roi_W))
    unique_freqs = np.unique(freqs)  # mw_steps 개의 주파수 값

    # 각 ROI 픽셀에 대해 ODMR 스펙트럼 재구성
    for i in range(roi_H):
        for j in range(roi_W):
            spectrum = roi_cube[:, i, j]
            avg_intensities = []
            for uf in unique_freqs:
                mask = (freqs == uf)
                avg_intensity = np.mean(spectrum[mask])
                avg_intensities.append(avg_intensity)
            avg_intensities = np.array(avg_intensities)
            min_index = np.argmin(avg_intensities)
            f_res = unique_freqs[min_index]
            Bz_map[i, j] = (f_res - D_ref) / gamma_e

    plt.figure()
    plt.imshow(Bz_map * 1e3, cmap='jet', origin='lower')
    plt.colorbar(label='Magnetic Field Bz (mT)')
    plt.xlabel("ROI Pixel X")
    plt.ylabel("ROI Pixel Y")
    plt.title("Magnetic Field Map (Bz) from ODMR (ROI)")
    plt.show()

# -----------------------------------------
# 쓰레드 시작 및 데이터 수집 완료
# -----------------------------------------
sdg_thread = threading.Thread(target=sdg_control, daemon=True)
producer_thread = threading.Thread(target=camera_producer, daemon=True)

sdg_thread.start()
producer_thread.start()

producer_thread.join()
sdg_thread.join()

# Magnetic image processing (ROI 적용)
magnetic_image_processing()

# -----------------------------------------
# CSV 파일로 Raw Data 저장 (Frequency별 intensity 기록)
# -----------------------------------------
import glob
today_str = datetime.datetime.today().strftime("%m%d%Y")
data_dir = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
pattern = os.path.join(data_dir, f"{today_str}_*.csv")
existing_files = glob.glob(pattern)
counter = len(existing_files) + 1
csv_filename = os.path.join(data_dir, f"{today_str}_{counter}.csv")

# intensity_dict 구성 (각 주파수별 ROI 내 총 intensity 합)
intensity_dict = {}
for frame_num, img in zip(range(1, n_frames+1), image_list):
    roi = img[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
    total_intensity = np.sum(roi)
    step_index = (frame_num - 1) % mw_steps
    freq = mw_start + step_index * mw_step
    if freq in intensity_dict:
        intensity_dict[freq].append(total_intensity)
    else:
        intensity_dict[freq] = [total_intensity]

max_cycles = max(len(vals) for vals in intensity_dict.values())
with open(csv_filename, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    header = ["MW Frequency (GHz)"] + [f"cycle{i+1}" for i in range(max_cycles)]
    writer.writerow(header)
    for freq in sorted(intensity_dict.keys()):
        row = [freq/1e9] + intensity_dict[freq] + [""] * (max_cycles - len(intensity_dict[freq]))
        writer.writerow(row)

print(f"Raw intensity data가 {csv_filename} 파일로 저장되었습니다.")
