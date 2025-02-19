import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
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
intensity_dict = {}  # key: frequency (Hz), value: list of intensity values

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
    """
    SDG2082x를 PyVISA로 제어하여 25Hz 펄스를 출력합니다.
    이 장비는 25Hz 펄스를 발생시켜 SynthHD와 카메라를 동시에 트리거합니다.
    카메라가 준비되었음을 나타내는 camera_ready 플래그가 True가 될 때까지 대기한 후,
    Producer가 n_frames(500프레임)을 수집할 때까지 대기하고,
    수집 완료 후 SDG2082x를 종료합니다.
    """
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
        sdg.write("C1:BSWV FRQ,25")         # 펄스 주파수를 25Hz (즉, 40ms 주기)로 설정
        sdg.write("C1:BSWV AMP,2")          # 진폭 2V
        sdg.write("C1:BSWV OFST,1")         # DC offset 1V
        sdg.write("C1:BSWV WIDTH,2e-4")      # 펄스 폭 2e-4 (단위는 모델에 따라 해석)
        sdg.write("C1:OUTP ON")             # 출력 활성화
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    # 카메라 준비 플래그가 True가 될 때까지 대기
    while not camera_ready:
        time.sleep(0.01)
    print("카메라 준비 완료. SDG2082x 펄스 출력 시작")

    # Producer가 n_frames (500프레임) 수집될 때까지 대기
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
    """
    하드웨어 트리거(예: SDG2082x의 25Hz 펄스)가 들어올 때마다 Zelux 카메라가 프레임을 획득하고,
    (프레임 번호, 이미지 배열) 튜플을 큐에 저장합니다.
    """
    global frames_captured, camera_ready
    with TLCameraSDK() as sdk:
        available_cameras = sdk.discover_available_cameras()
        if len(available_cameras) < 1:
            print("카메라가 감지되지 않았습니다.")
            return
        with sdk.open_camera(available_cameras[0]) as camera:
            camera.exposure_time_us = 20000  # 20 ms 노출
            camera.frames_per_trigger_zero_for_unlimited = 1  # 트리거당 1프레임
            camera.image_poll_timeout_ms = 1000
            camera.frame_rate_control_value = 25      # 25Hz 트리거와 일치
            camera.is_frame_rate_control_enabled = True

            # operation_mode는 ARM 전에 설정해야 함
            camera.operation_mode = OPERATION_MODE.HARDWARE_TRIGGERED
            camera.arm(2)
            print("카메라 ARM: 하드웨어 트리거 대기 중")
            camera_ready = True  # 카메라가 준비되었음을 표시

            try:
                while True:
                    with capture_lock:
                        if frames_captured >= n_frames:
                            break
                    frame = camera.get_pending_frame_or_null()
                    if frame is not None:
                        image_buffer_copy = np.copy(frame.image_buffer)
                        numpy_shaped_image = image_buffer_copy.reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        # (프레임 번호, 이미지 배열) 튜플을 큐에 저장
                        frame_queue.put((frame.frame_count, numpy_shaped_image))
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{frame.frame_count} 저장 (총 {frames_captured}/{n_frames})")
                    # 프레임이 아직 없으면 바로 루프 반복
            except KeyboardInterrupt:
                print("카메라 Producer 종료 (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# 카메라 Consumer 함수
# -----------------------------------------
def camera_consumer():
    """
    Producer에서 큐에 저장된 프레임을 하나씩 꺼내어,
    각 프레임의 전체 intensity(모든 픽셀의 합)를 계산하고,
    해당 MW frequency (3GHz ~ 4GHz, 10MHz step)에 대응시켜 intensity_dict에 저장합니다.
    MW frequency는 (frame_count - 1) mod 100를 사용하여 결정합니다.
    """
    global measurement_complete, intensity_dict
    intensity_dict = {}
    processed_frames = 0
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            total_intensity = np.sum(image)
            step_index = (frame_num - 1) % mw_steps
            freq = mw_start + step_index * mw_step  # frequency in Hz
            if freq in intensity_dict:
                intensity_dict[freq].append(total_intensity)
            else:
                intensity_dict[freq] = [total_intensity]
            processed_frames += 1
            print(f"프레임 #{frame_num} 처리: MW freq = {freq/1e9:.3f} GHz, 총 intensity = {total_intensity}")
        except queue.Empty:
            continue
    measurement_complete = True

# -----------------------------------------
# 쓰레드 시작 및 측정 완료 후 최종 결과 그래프 출력
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

# 최종 데이터: 각 MW frequency에 대해 여러 intensity 값이 있으면 평균내기
frequencies = sorted(intensity_dict.keys())
avg_intensities = [np.mean(intensity_dict[freq]) for freq in frequencies]

# 최종 결과 그래프: x축은 MW frequency (GHz), y축은 평균 intensity
plt.figure()
plt.plot(np.array(frequencies)/1e9, avg_intensities, 'ro-')
plt.xlabel("MW Frequency (GHz)")
plt.ylabel("Average Total Intensity")
plt.grid(True)
plt.show()

# ---------------------------
# CSV 파일로 raw 데이터 저장 (각 frequency별 intensity 값 기록)
# ---------------------------
# 각 주파수에서의 측정 사이클 수의 최대값 계산
max_cycles = max(len(vals) for vals in intensity_dict.values())

csv_filename = "해당날짜_실험횟수.csv"
with open(csv_filename, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    header = ["MW Frequency (GHz)"] + [f"cycle{i+1}" for i in range(max_cycles)]
    writer.writerow(header)
    for freq in frequencies:
        row = [freq/1e9] + intensity_dict[freq] + [""] * (max_cycles - len(intensity_dict[freq]))
        writer.writerow(row)

print(f"Raw intensity data가 {csv_filename} 파일로 저장되었습니다.")
