import threading
import queue
import time
import numpy as np
import matplotlib.pyplot as plt
import csv
import datetime
import glob
from scipy.optimize import curve_fit
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, OPERATION_MODE
import pyvisa
import os

try:
    # 윈도우용 DLL 설정: windows_setup 모듈이 제공되면 사용하여 DLL 경로 추가
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None

# 전역 ROI 설정 (x: 550~1000, y: 400~800)
roi_y_start, roi_y_end = 400, 801
roi_x_start, roi_x_end = 550, 1001

# 측정할 프레임 수
n_frames = 500

# MW sweep 파라미터
mw_start = 3e9    # 3 GHz
mw_step = 1e7     # 10 MHz
mw_steps = 100    # 100 steps (3 GHz ~ 4 GHz)

# 외부 bias 자기장 (B0)
B0 = 10e-3  # 10 mT

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
        sdg.write("C1:BSWV FRQ,1")         # 1 Hz 펄스 → 1s 주기
        sdg.write("C1:BSWV AMP,2")
        sdg.write("C1:BSWV OFST,1")
        sdg.write("C1:BSWV WIDTH,2e-4")
    except Exception as e:
        print("SDG 초기 설정 오류:", e)
        return

    print("SDG2082x 제어 시작: 카메라 준비 대기 중...")
    while not camera_ready:
        time.sleep(0.01)
    print("카메라 준비 완료. SDG2082x 펄스 출력 시작")
    sdg.write("C1:OUTP ON")
    
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
# Lorentzian 피팅 함수 (두 개의 dip)
# -----------------------------------------
def double_lorentzian_dip(f, f1, A1, gamma1, f2, A2, gamma2, offset):
    # 피크가 dip이므로 offset에서 값을 빼는 형태
    return offset - (A1 * gamma1**2 / ((f - f1)**2 + gamma1**2)) - (A2 * gamma2**2 / ((f - f2)**2 + gamma2**2))

# -----------------------------------------
# Magnetic Image Processing 함수 (ROI 적용, Lorentzian 피팅 이용)
# -----------------------------------------
def magnetic_image_processing():
    if len(image_list) == 0:
        print("수집된 이미지가 없습니다.")
        return
    cube = np.stack(image_list, axis=0)  # shape: (n_frames, H, W)
    n, H, W = cube.shape

    # ROI 영역 추출
    roi_cube = cube[:, roi_y_start:roi_y_end, roi_x_start:roi_x_end]
    roi_H, roi_W = roi_cube.shape[1], roi_cube.shape[2]
    freqs = np.array(mw_frequencies)  # (n_frames,)

    # 기본 파라미터
    gamma_e = 28e9  # 28 GHz/T
    B0 = 10e-3     # 10 mT bias field
    # (D_ref는 사용하지 않고, f1 and f2를 직접 피팅하여 사용)

    # unique 주파수 (MW sweep의 100개 포인트)
    unique_freqs = np.sort(np.unique(freqs))

    # 결과 ΔB_z map (ROI 내 각 픽셀에 대해)
    deltaBz_map = np.zeros((roi_H, roi_W))

    # for each ROI pixel, reconstruct the spectrum and perform double Lorentzian fit
    for i in range(roi_H):
        for j in range(roi_W):
            # 각 픽셀의 스펙트럼: 각 프레임에서 해당 픽셀 intensity
            spectrum = roi_cube[:, i, j]
            # 그룹화: 각 unique frequency에 대한 평균 intensity
            y_data = []
            for uf in unique_freqs:
                mask = (freqs == uf)
                y_data.append(np.mean(spectrum[mask]))
            y_data = np.array(y_data)
            x_data = unique_freqs  # in Hz

            # 초기 추정값: f1, f2 around the center of the sweep
            # 가령, f1_init = 3.47 GHz, f2_init = 3.49 GHz, A1, A2 추정은 dip depth (offset - min), gamma ~ 1e7, offset는 max intensity
            offset_init = np.max(y_data)
            min_val = np.min(y_data)
            A_init = offset_init - min_val  # dip depth
            f1_init = 3.47e9
            f2_init = 3.49e9
            gamma1_init = 5e7  # 50 MHz
            gamma2_init = 5e7

            p0 = [f1_init, A_init, gamma1_init, f2_init, A_init, gamma2_init, offset_init]

            try:
                popt, pcov = curve_fit(double_lorentzian_dip, x_data, y_data, p0=p0)
                # popt = [f1, A1, gamma1, f2, A2, gamma2, offset]
                f1, f2 = popt[0], popt[3]
                # f_plus = max(f1, f2), f_minus = min(f1, f2)
                f_plus = max(f1, f2)
                f_minus = min(f1, f2)
                Bz = (f_plus - f_minus) / (2 * gamma_e)  # in Tesla
                deltaBz = Bz - B0
            except Exception as e:
                # 만약 피팅 실패하면, 대신 단순 최소값 접근법 사용
                print(f"픽셀 ({i},{j}) 피팅 실패: {e}")
                min_index = np.argmin(y_data)
                f_res = x_data[min_index]
                # 이 경우, 임의로 f1=f_res, f2=f_res (즉, Bz=0)
                deltaBz = -B0
            deltaBz_map[i, j] = deltaBz

    # Magnetic image plot (ΔB_z map, 단위 mT)
    plt.figure()
    plt.imshow(deltaBz_map * 1e3, cmap='jet', origin='lower')
    plt.colorbar(label='ΔBz (mT)')
    plt.xlabel("ROI Pixel X")
    plt.ylabel("ROI Pixel Y")
    plt.title("ΔBz Map from Double Lorentzian Fitting (CW ODMR)")
    plt.show()

# -----------------------------------------
# 쓰레드 시작 및 데이터 수집
# -----------------------------------------
sdg_thread = threading.Thread(target=sdg_control, daemon=True)
producer_thread = threading.Thread(target=camera_producer, daemon=True)

sdg_thread.start()
producer_thread.start()

producer_thread.join()
sdg_thread.join()

# Magnetic image processing (ROI, Lorentzian 피팅 이용)
magnetic_image_processing()

# -----------------------------------------
# CSV 파일로 Raw Data 저장 (각 frequency별 ROI 내 총 intensity 합)
# -----------------------------------------
today_str = datetime.datetime.today().strftime("%m%d%Y")
data_dir = r"C:\Users\user\Documents\hBN_magnetrometry\Data"
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
pattern = os.path.join(data_dir, f"{today_str}_*.csv")
existing_files = glob.glob(pattern)
counter = len(existing_files) + 1
csv_filename = os.path.join(data_dir, f"{today_str}_{counter}.csv")

# intensity_dict 구성: 각 프레임에서 ROI 내 총 intensity 합을, MW 주파수별로 그룹화
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
