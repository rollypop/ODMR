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
import pandas as pd  # pandas 임포트

# 설정
n_frames = 20000  # 측정할 총 프레임 수 (대용량 측정)
mw_start = 3e9    # 시작 MW 주파수: 3 GHz
mw_step = 5e7     # 주파수 스텝: 50 MHz
mw_steps = 20     # 20 스텝 (3 GHz ~ 4 GHz)

# 카메라 프레임 데이터와 intensity 데이터 저장용 자료구조
frame_queue = queue.Queue(maxsize=n_frames)
intensity_dict = {}  # key: MW 주파수 (Hz), value: list of ROI 총 intensity 값

# 전역 변수 및 동기화를 위한 변수
frames_captured = 0
capture_lock = threading.Lock()
camera_ready = False  # 카메라가 ARM되면 True로 설정
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
        sdg.write("C1:BSWV FRQ,25")         # 25Hz 펄스 → 40ms 주기
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
    sdg.write("C1:OUTP ON")  # 출력 활성화

    global frames_captured
    while True:
        with capture_lock:
            if frames_captured >= n_frames:
                break
        time.sleep(0.01)
    sdg.close()
    print("SDG 제어 종료: 20000 프레임 수집 후 SDG 꺼짐")

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
            camera_ready = True  # 카메라 준비 완료

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
                        # 디버깅: 매 1000프레임마다 진행상황 출력
                        if frames_captured % 1000 == 0:
                            print(f"DEBUG: 현재까지 {frames_captured} 프레임 수집됨")
                        # 프레임 번호 및 현재까지 수집된 프레임 정보 출력
                        print(f"프레임 #{frame.frame_count} 저장 (총 {frames_captured}/{n_frames})")
            except KeyboardInterrupt:
                print("카메라 Producer 종료 (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# 카메라 Consumer 함수 (ROI 영역 처리: 총 intensity 합 계산 및 디버깅 로그 추가)
# -----------------------------------------
def camera_consumer():
    global measurement_complete, intensity_dict
    intensity_dict = {}
    processed_frames = 0
    # ROI 영역 설정: x: 550~1000, y: 400~800 (Python index: y 400:801, x 550:1001)
    roi_y_start, roi_y_end = 400, 801
    roi_x_start, roi_x_end = 550, 1001
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            # ROI 영역 추출
            roi = image[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            roi_total_intensity = np.sum(roi)
            # MW 주파수 계산: (frame_num - 1) % mw_steps 사용 (Hz 단위)
            step_index = (frame_num - 1) % mw_steps
            freq = mw_start + step_index * mw_step  # Hz
            if freq in intensity_dict:
                intensity_dict[freq].append(roi_total_intensity)
            else:
                intensity_dict[freq] = [roi_total_intensity]
            processed_frames += 1
            # 디버깅: 각 500프레임마다 데이터 수 확인
            if processed_frames % 500 == 0:
                print(f"DEBUG: 소비된 프레임 수 {processed_frames}, 현재 intensity_dict의 항목 수: {len(intensity_dict)}")
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

# 디버깅: intensity_dict에 저장된 데이터 개수 확인
for freq in sorted(intensity_dict.keys()):
    print(f"DEBUG: 주파수 {freq/1e9:.3f} GHz에 측정된 데이터 개수: {len(intensity_dict[freq])}")

# 각 MW 주파수별 평균 intensity 계산 (각 주파수 당 1000회 측정이 목표)
frequencies = sorted(intensity_dict.keys())
avg_intensities = [np.mean(intensity_dict[freq]) for freq in frequencies]

# -----------------------------------------
# Pandas를 사용하여 CSV 파일로 raw 데이터 저장
# -----------------------------------------
data_dir = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
today_str = datetime.datetime.today().strftime("%m%d%Y")
pattern = os.path.join(data_dir, f"{today_str}_*.csv")
existing_files = glob.glob(pattern)
counter = len(existing_files) + 1
csv_filename = os.path.join(data_dir, f"{today_str}_{counter}.csv")

# 각 주파수별 측정값을 DataFrame으로 생성
data = {}
max_cycles = max(len(vals) for vals in intensity_dict.values())
for freq in sorted(intensity_dict.keys()):
    # 각 주파수에 대한 측정값 리스트, 길이가 max_cycles가 안되면 NaN으로 채움
    row = intensity_dict[freq] + [np.nan] * (max_cycles - len(intensity_dict[freq]))
    data[freq/1e9] = row

df = pd.DataFrame(data).T
df.index.name = "MW Frequency (GHz)"
df.columns = [f"cycle{i+1}" for i in range(max_cycles)]

# CSV 파일로 저장
df.to_csv(csv_filename)
print(f"Raw intensity data가 {csv_filename} 파일로 저장되었습니다.")
