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
    # if on Windows, use the provided setup script to add the DLLs folder to the PATH
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

# 카메라 프레임 데이터를 저장할 큐와, 각 MW frequency별 intensity를 저장할 딕셔너리
frame_queue = queue.Queue(maxsize=500)
intensity_dict = {}  # key: frequency (Hz), value: list of ROI total intensity values

# 전역 카운터 및 동기화를 위한 변수
frames_captured = 0
capture_lock = threading.Lock()

# 카메라 준비 플래그 (Producer가 ARM되면 True로 설정)
camera_ready = False

# 측정 완료 여부 (Consumer에서 n_frames 처리 후 True 설정)
measurement_complete = False

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
        sdg.write("C1:BSWV WVTP,PULSE")   # 펄스 모드 선택
        sdg.write("C1:BSWV FRQ,1")         # 1 Hz 펄스 → 1s 주기
        sdg.write("C1:BSWV AMP,2")
        sdg.write("C1:BSWV OFST,1")
        sdg.write("C1:BSWV WIDTH,2e-4")      # 펄스 폭 2µs (예시)
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    while not camera_ready:
        time.sleep(0.01)
    print("카메라 준비 완료. SDG2082x 펄스 출력 시작")
    sdg.write("C1:OUTP ON")             # 출력 활성화
    
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
            camera.exposure_time_us = 200000  # 200 ms 노출
            camera.frames_per_trigger_zero_for_unlimited = 1
            camera.image_poll_timeout_ms = 1000
            camera.frame_rate_control_value = 1
            camera.is_frame_rate_control_enabled = True

            # 반드시 operation_mode는 ARM 전에 설정해야 함
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
                        image_buffer_copy = np.copy(frame.image_buffer)
                        img = image_buffer_copy.reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        frame_queue.put((frame.frame_count, img))
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{frame.frame_count} 저장 (총 {frames_captured}/{n_frames})")
            except KeyboardInterrupt:
                print("카메라 Producer 종료 (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# 카메라 Consumer 함수 (ROI 영역 처리: 총 intensity 합)
# -----------------------------------------
def camera_consumer():
    global measurement_complete, intensity_dict
    intensity_dict = {}
    processed_frames = 0
    # ROI 영역: x: 550~1000, y: 400~800 (Python index: y 400:801, x 550:1001)
    roi_y_start, roi_y_end = 400, 801
    roi_x_start, roi_x_end = 550, 1001
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            # ROI 추출
            roi = image[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            # ROI 내 전체 픽셀의 intensity 합 계산
            roi_total_intensity = np.sum(roi)
            # MW frequency 계산: (frame_num - 1) mod mw_steps를 사용하여 MW 주파수 결정
            step_index = (frame_num - 1) % mw_steps
            freq = mw_start + step_index * mw_step  # Hz 단위
            if freq in intensity_dict:
                intensity_dict[freq].append(roi_total_intensity)
            else:
                intensity_dict[freq] = [roi_total_intensity]
            processed_frames += 1
            print(f"프레임 #{frame_num} 처리: MW freq = {freq/1e9:.3f} GHz, ROI 총 intensity = {roi_total_intensity}")
        except queue.Empty:
            continue
    measurement_complete = True

# -----------------------------------------
# 쓰레드 시작 및 데이터 수집 완료
# -----------------------------------------
sdg_thread = threading.Thread(target=sdg_control, daemon=True)
producer_thread = threading.Thread(target=camera_producer, daemon=True)
consumer_thread = threading.Thread(target=camera_consumer, daemon=True)

sdg_thread.start()
producer_thread.start()
consumer_thread.start()

producer_thread.join()
consumer_thread.join()
sdg_thread.join()

# 각 MW frequency에 대해 여러 intensity 값이 있으면 평균내기
frequencies = sorted(intensity_dict.keys())
avg_intensities = [np.mean(intensity_dict[freq]) for freq in frequencies]

plt.figure()
plt.plot(np.array(frequencies)/1e9, avg_intensities, 'ro-')
plt.xlabel("MW Frequency (GHz)")
plt.ylabel("ROI Total Intensity (a.u.)")
plt.grid(True)
plt.show()

# -----------------------------------------
# CSV 파일로 raw 데이터 저장 (각 frequency별 intensity 값 기록)
# -----------------------------------------
import glob
# CSV 파일 저장 경로 및 파일명 생성
data_dir = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
today_str = datetime.datetime.today().strftime("%m%d%Y")
pattern = os.path.join(data_dir, f"{today_str}_*.csv")
existing_files = glob.glob(pattern)
counter = len(existing_files) + 1
csv_filename = os.path.join(data_dir, f"{today_str}_{counter}.csv")

max_cycles = max(len(vals) for vals in intensity_dict.values())
with open(csv_filename, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    header = ["MW Frequency (GHz)"] + [f"cycle{i+1}" for i in range(max_cycles)]
    writer.writerow(header)
    for freq in sorted(intensity_dict.keys()):
        row = [freq/1e9] + intensity_dict[freq] + [""] * (max_cycles - len(intensity_dict[freq]))
        writer.writerow(row)

print(f"Raw intensity data가 {csv_filename} 파일로 저장되었습니다.")
