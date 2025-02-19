import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pyvisa
import os

# 윈도우용 DLL 설정: windows_setup 모듈이 제공되면 이를 사용하여 DLL 경로를 추가
try:
    from windows_setup import configure_path
    configure_path()
except ImportError:
    pass

# 측정할 프레임 수
n_frames = 500

# MW sweep 파라미터
mw_start = 3e9    # 3 GHz
mw_step = 1e7     # 10 MHz
mw_steps = 100    # 100 step cycle: 3 GHz ~ 4 GHz

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
        sdg.write("C1:BSWV WVTP,PULSE")   # 펄스 모드 선택
        sdg.write("C1:BSWV FRQ,25")         # 25 Hz 펄스 → 40ms 주기
        sdg.write("C1:BSWV AMP,2")          # 진폭 2V
        sdg.write("C1:BSWV OFST,1")         # DC offset 1V
        sdg.write("C1:BSWV WIDTH,2e-4")      # 펄스 폭 (모델에 따라 해석; 여기서는 예시)
        sdg.write("C1:OUTP ON")             # 출력 활성화
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    # 카메라가 준비되었음을 나타내는 camera_ready가 True가 될 때까지 대기
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
            camera.frames_per_trigger_zero_for_unlimited = 1  # 트리거당 1프레임
            camera.image_poll_timeout_ms = 1000
            camera.frame_rate_control_value = 25      # 25 Hz 트리거에 맞춤
            camera.is_frame_rate_control_enabled = True

            # operation_mode는 ARM 전에 설정해야 함
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
                        # 이미지 버퍼를 numpy 배열로 복사하고 2D 배열로 reshape
                        img = np.copy(frame.image_buffer).reshape(
                            camera.image_height_pixels, camera.image_width_pixels
                        )
                        image_list.append(img)
                        # MW frequency: 각 프레임 번호에 따라, 
                        # freq = mw_start + ((frame_count - 1) mod mw_steps) * mw_step
                        current_frame = frame.frame_count
                        freq = mw_start + ((current_frame - 1) % mw_steps) * mw_step
                        mw_frequencies.append(freq)
                        with capture_lock:
                            frames_captured += 1
                        print(f"프레임 #{current_frame} 저장 (총 {frames_captured}/{n_frames})")
                    # 프레임이 아직 없으면 바로 루프 반복
            except KeyboardInterrupt:
                print("카메라 Producer 종료 (KeyboardInterrupt)")
            finally:
                camera.disarm()

# -----------------------------------------
# 카메라 Consumer 함수 (Magnetic Image 생성)
# -----------------------------------------
def magnetic_image_processing():
    """
    수집된 image_list와 mw_frequencies를 이용해 데이터 큐브를 구성합니다.
    각 픽셀에 대해, 해당 픽셀의 intensity를 MW 주파수에 따라 재구성하고,
    최소 intensity가 나타나는 주파수를 공명 주파수로 가정합니다.
    기준 D_ref와 비교해 Zeeman shift로부터 Bz를 계산하여 2D 자기장 맵(Bz_map)을 생성합니다.
    """
    # 데이터 큐브 구성: (n_frames, H, W)
    if len(image_list) == 0:
        print("수집된 이미지가 없습니다.")
        return
    cube = np.stack(image_list, axis=0)
    n, H, W = cube.shape

    # MW 주파수 배열: shape (n_frames,)
    freqs = np.array(mw_frequencies)  # in Hz

    # 기본 파라미터
    gamma_e = 28e9  # 28 GHz/T
    D_ref = 3.48e9  # 기준 ODMR 중심 주파수, 3.48 GHz (예시)

    # 결과 자기장 맵
    Bz_map = np.zeros((H, W))

    # 각 픽셀에 대해 ODMR 스펙트럼 재구성
    # for each pixel, group intensity data by unique MW frequency (averaging over repeated cycles)
    unique_freqs = np.unique(freqs)  # (mw_steps개, 100개)
    for i in range(H):
        for j in range(W):
            # 추출: 각 프레임에서 해당 픽셀의 intensity
            spectrum = cube[:, i, j]
            avg_intensities = []
            for uf in unique_freqs:
                mask = (freqs == uf)
                avg_intensity = np.mean(spectrum[mask])
                avg_intensities.append(avg_intensity)
            avg_intensities = np.array(avg_intensities)
            # 최소 intensity가 ODMR dip라 가정
            min_index = np.argmin(avg_intensities)
            f_res = unique_freqs[min_index]  # 해당 픽셀의 공명 주파수
            # Zeeman shift로부터 자기장 계산: Bz = (f_res - D_ref) / gamma_e
            Bz_map[i, j] = (f_res - D_ref) / gamma_e

    # Plot magnetic image (Bz_map in mT)
    plt.figure()
    plt.imshow(Bz_map * 1e3, cmap='jet', origin='lower')
    plt.colorbar(label='Magnetic Field Bz (mT)')
    plt.xlabel("Pixel X")
    plt.ylabel("Pixel Y")
    plt.title("Magnetic Field Map (Bz) from ODMR")
    plt.show()

# -----------------------------------------
# 쓰레드 시작 및 최종 데이터 처리
# -----------------------------------------
sdg_thread = threading.Thread(target=sdg_control, daemon=True)
producer_thread = threading.Thread(target=camera_producer, daemon=True)

sdg_thread.start()
producer_thread.start()

producer_thread.join()
sdg_thread.join()

# 수집이 완료된 후, magnetic image processing 실행
magnetic_image_processing()
