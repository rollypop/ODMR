import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pyvisa
import os
import csv
import datetime

# SynthHD 제어를 위한 패키지 임포트 (공식 배포하는 패키지 사용)
from synth_hd import SynthHD  # synth_hd.py에 정의된 클래스를 사용한다고 가정

try:
    # if on Windows, use the provided setup script to add the DLLs folder to the PATH
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None

# 측정할 프레임 수 (예시로 100000 프레임)
n_frames = 20000

# MW sweep 파라미터
mw_start = 3e9    # 3 GHz
mw_step = 5e7     # 50 MHz
mw_steps = 20    # 20 steps (3 GHz ~ 4 GHz)

# 카메라 프레임 데이터를 저장할 큐와, 각 MW frequency별 intensity를 저장할 딕셔너리
frame_queue = queue.Queue(maxsize=20000)
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
    이 장비는 25Hz 펄스를 발생시켜 SynthHD, 카메라를 동시에 트리거합니다.
    카메라 준비 플래그가 True가 될 때까지 대기한 후, 
    카메라가 n_frames(100000 프레임)을 수집할 때까지 기다린 후 종료합니다.
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
        sdg.write("C1:BSWV FRQ,1")         # 25Hz (40ms 주기)
        sdg.write("C1:BSWV AMP,2")         # 진폭 2V
        sdg.write("C1:BSWV OFST,1")        # DC offset 1V
        sdg.write("C1:BSWV WIDTH,2e-4")     # 펄스 폭 설정
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    # 카메라 준비 플래그가 True가 될 때까지 대기
    while not camera_ready:
        time.sleep(0.01)
    print("카메라 준비 완료. SDG2082x 펄스 출력 시작")
    sdg.write("C1:OUTP ON")

    # Producer가 n_frames 수집될 때까지 대기
    global frames_captured
    while True:
        with capture_lock:
            if frames_captured >= n_frames:
                break
        time.sleep(0.01)
    sdg.close()
    print("SDG 제어 종료: 100000프레임 수집 후 SDG 꺼짐")

# -----------------------------------------
# SynthHD 제어 함수 (Python 패키지 이용)
# -----------------------------------------
def synthhd_control():
    """
    SynthHD를 제어하여, 카메라가 18번째 프레임부터 MW 방출을 시작하도록 합니다.
    카메라가 18번째 프레임에 도달하기 전까지 MW 출력은 OFF 상태를 유지하며,
    이후 프레임 번호에 따라 MW 주파수를 업데이트합니다.
    """
    # 실제 연결된 포트를 확인 후 devpath에 지정하세요.
    devpath = "COM3"  # 예: "COM3"
    try:
        # SynthHD 클래스는 devpath를 필수 인자로 요구하며, 생성 시 자동으로 연결됨
        synth = SynthHD(devpath)
    except Exception as e:
        print("SynthHD 초기화 오류:", e)
        return

    try:
        synth.set_frequency(mw_start)
        synth.output_off()  # 초기 MW 출력 OFF
    except Exception as e:
        print("SynthHD 초기 설정 오류:", e)
        return

    print("SynthHD: 카메라가 18번째 프레임에 도달할 때까지 대기합니다...")
    while True:
        with capture_lock:
            if frames_captured >= 18:
                break
        time.sleep(0.01)
    print("SynthHD: 카메라 18번째 프레임 도달. MW 출력 시작합니다.")
    try:
        synth.output_on()
    except Exception as e:
        print("SynthHD 출력 ON 오류:", e)
    
    last_frame = 18
    while True:
        with capture_lock:
            current_frame = frames_captured
        if current_frame >= n_frames:
            break
        if current_frame > last_frame:
            last_frame = current_frame
            step_index = (current_frame - 1) % mw_steps
            new_freq = mw_start + step_index * mw_step
            try:
                synth.set_frequency(new_freq)
                print(f"SynthHD: 프레임 {current_frame}, 주파수 {new_freq/1e9:.3f} GHz 설정")
            except Exception as e:
                print("SynthHD 주파수 업데이트 오류:", e)
        time.sleep(0.005)
    
    try:
        synth.output_off()
        synth.disconnect()
    except Exception as e:
        print("SynthHD 종료 오류:", e)
    print("SynthHD: MW 출력 OFF 및 장비 연결 종료")
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
            camera.frame_rate_control_value = 25      # 25Hz 트리거에 맞춤
            camera.is_frame_rate_control_enabled = True

            # operation_mode는 ARM 전에 설정해야 함
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
                        numpy_shaped_image = image_buffer_copy.reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        # (프레임 번호, 이미지 배열) 튜플을 큐에 저장
                        frame_queue.put((frame.frame_count, numpy_shaped_image))
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{frame.frame_count} 저장 (총 {frames_captured}/{n_frames})")
                    # 프레임이 없으면 루프 반복
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
    단, 초기 17프레임은 준비 단계로 간주하여 무시하고, 18번째 프레임부터 데이터를 처리합니다.
    """
    global measurement_complete, intensity_dict
    intensity_dict = {}
    processed_frames = 0
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            # 초기 17프레임은 무시
            if frame_num < 18:
                continue

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
synthhd_thread = threading.Thread(target=synthhd_control, daemon=True)

sdg_thread.start()
producer_thread.start()
consumer_thread.start()
synthhd_thread.start()

producer_thread.join()
consumer_thread.join()
synthhd_thread.join()
sdg_thread.join()

# 각 MW frequency에 대해 여러 intensity 값이 있으면 평균내기
frequencies = sorted(intensity_dict.keys())
avg_intensities = [np.mean(intensity_dict[freq]) for freq in frequencies]

plt.figure()
plt.plot(np.array(frequencies)/1e9, avg_intensities, 'ro-')
plt.xlabel("MW Frequency (GHz)")
plt.ylabel("Average Total Intensity")
plt.grid(True)
plt.show()

# CSV 파일로 raw 데이터 저장 (각 frequency별 intensity 값 기록)
today_str = datetime.datetime.today().strftime("%m%d%Y")
data_dir = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
pattern = os.path.join(data_dir, f"{today_str}_*.csv")
import glob
existing_files = glob.glob(pattern)
counter = len(existing_files) + 1
csv_filename = os.path.join(data_dir, f"{today_str}_{counter}.csv")

max_cycles = max(len(vals) for vals in intensity_dict.values())
with open(csv_filename, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    header = ["MW Frequency (GHz)"] + [f"cycle{i+1}" for i in range(max_cycles)]
    writer.writerow(header)
    for freq in frequencies:
        row = [freq/1e9] + intensity_dict[freq] + [""] * (max_cycles - len(intensity_dict[freq]))
        writer.writerow(row)

print(f"Raw intensity data가 {csv_filename} 파일로 저장되었습니다.")
