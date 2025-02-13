import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pyvisa
import os
os.environ["PATH"] = r"C:\Users\user\Documents\hBN magnetrometry\Scientific Camera Interfaces\SDK\Python Toolkit\dlls\64_lib\thorlabs_tsi_camera_sdk.dll" + os.environ["PATH"]

# 측정할 프레임 수 (예: 10 프레임 측정)
n_frames = 500

# 카메라 프레임 데이터를 저장할 큐와 intensity 데이터를 저장할 리스트
frame_queue = queue.Queue(maxsize=500)
intensity_data = []

# 카메라 측에서 프레임 획득 개수를 위한 글로벌 변수
frames_captured = 0
capture_lock = threading.Lock()

# -----------------------------------------
# SDG2082x 제어 함수 (PyVISA 이용)
# -----------------------------------------
def sdg_control():
    """
    SDG2082x를 PyVISA를 이용해 제어하는 함수입니다.
    이 장비는 1Hz 펄스를 발생시켜 SynthHD와 카메라를 동시에 트리거하는 역할을 합니다.
    MW sweep은 SynthHD에서 별도로 진행되므로, 여기서는 단순히 초기 설정 후 1Hz 간격으로 대기합니다.
    """
    rm = pyvisa.ResourceManager()
    try:
        sdg = rm.open_resource("USB0::0xF4EC::0xEE38::SDG2XCAD1R2393::INSTR")
    except Exception as e:
        print("SDG2082x 연결 실패:", e)
        return

    try:
        sdg.write("*RST")  # 리셋
        time.sleep(0.1)
        sdg.write("C1:BSWV WVTP,PULSE")
        sdg.write("C1:BSWV FRQ,1")
        sdg.write("C1:BSWV AMP,2")
        sdg.write("C1:BSWV OFST,1")
        sdg.write("C1:BSWV WIDTH,2e-6")
        sdg.write("C1:OUTP ON")
        # MW sweep은 SynthHD에서 처리하므로, 여기서는 별도 주파수 업데이트 없이 1Hz 트리거 환경만 유지
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 1Hz 펄스 환경 유지 (측정 기간 동안 대기)")
    start_time = time.time()
    while time.time() - start_time < n_frames:
        time.sleep(1)  # 1Hz 트리거 주기를 시뮬레이션
    sdg.close()
    print("SDG 제어 종료")

# -----------------------------------------
# 카메라 Producer 함수
# -----------------------------------------
def camera_producer():
    """
    하드웨어 트리거(예: SDG2082x의 1Hz 펄스)가 들어올 때마다 Zelux 카메라가 프레임을 획득하고,
    (프레임 번호, 이미지 데이터) 튜플을 큐에 저장합니다.
    """
    global frames_captured
    with TLCameraSDK() as sdk:
        available_cameras = sdk.discover_available_cameras()
        if len(available_cameras) < 1:
            print("카메라가 감지되지 않았습니다.")
            return
        with sdk.open_camera(available_cameras[0]) as camera:
            camera.exposure_time_us = 300000  # 300 ms 노출
            camera.frames_per_trigger_zero_for_unlimited = 1  # 트리거당 1프레임
            camera.image_poll_timeout_ms = 1000
            camera.frame_rate_control_value = 1  # 1Hz 트리거와 일치
            camera.is_frame_rate_control_enabled = True

            # 반드시 operation_mode는 ARM 전에 설정해야 함!
            camera.operation_mode = OPERATION_MODE.HARDWARE_TRIGGERED
            camera.arm(2)
            print("카메라 ARM: 하드웨어 트리거 대기 중")
            try:
                while True:
                    with capture_lock:
                        if frames_captured >= n_frames:
                            break
                    frame = camera.get_pending_frame_or_null()
                    if frame is not None:
                        # 이미지 버퍼 복사 후 2차원 배열로 변환
                        image_buffer_copy = np.copy(frame.image_buffer)
                        numpy_shaped_image = image_buffer_copy.reshape(camera.image_height_pixels,
                                                                       camera.image_width_pixels)
                        # 여기서는 3채널 이미지로 변환하지 않고 그레이스케일 데이터를 사용해도 됨
                        # Producer는 (프레임 번호, 이미지 배열) 튜플로 저장
                        frame_queue.put((frame.frame_count, numpy_shaped_image))
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{frame.frame_count} 저장 (총 {frames_captured}/{n_frames})")
                    # 프레임이 아직 없으면 딜레이 없이 루프 반복
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
    각 프레임의 전체 intensity(픽셀의 총합)를 계산하고, intensity_data 리스트에 저장합니다.
    """
    processed_frames = 0
    while processed_frames < n_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            total_intensity = np.sum(image)
            intensity_data.append(total_intensity)
            processed_frames += 1
            print(f"프레임 #{frame_num} 처리: 총 intensity = {total_intensity}")
        except queue.Empty:
            continue

# -----------------------------------------
# 쓰레드 시작 및 종료 후 최종 그래프 출력
# -----------------------------------------
sdg_thread = threading.Thread(target=sdg_control, daemon=True)
producer_thread = threading.Thread(target=camera_producer, daemon=True)
consumer_thread = threading.Thread(target=camera_consumer, daemon=True)

sdg_thread.start()
producer_thread.start()
consumer_thread.start()

# Producer와 Consumer 쓰레드가 완료될 때까지 대기
producer_thread.join()
consumer_thread.join()
sdg_thread.join()

# 측정이 모두 끝났으므로 최종 결과 그래프 출력
plt.figure()
plt.plot(np.arange(1, len(intensity_data) + 1), intensity_data, 'ro-')
plt.xlabel("Frame Number")
plt.ylabel("Total Intensity")
plt.grid(True)
plt.show()