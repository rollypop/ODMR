def camera_consumer():
    global measurement_complete, intensity_dict
    intensity_dict = {}
    processed_frames = 0
    last_frame_processed = 0
    effective_frames = n_frames - 17  # 초기 17프레임 제외
    # ROI 영역 설정: x: 550~1000, y: 400~800 (Python index: y 400:801, x 550:1001)
    roi_y_start, roi_y_end = 400, 801
    roi_x_start, roi_x_end = 550, 1001
    while processed_frames < effective_frames:
        try:
            frame_num, image = frame_queue.get(timeout=0.1)
            # 초기 17프레임은 건너뜀
            if frame_num < 18:
                continue
            # 중복된 프레임 번호가 나오면 건너뛰기
            if frame_num <= last_frame_processed:
                continue
            last_frame_processed = frame_num
            # ROI 영역 추출
            roi = image[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            roi_total_intensity = np.sum(roi)
            # MW 주파수 계산 (Hz 단위)
            step_index = (frame_num - 1) % mw_steps
            freq = mw_start + step_index * mw_step
            if freq in intensity_dict:
                intensity_dict[freq].append(roi_total_intensity)
            else:
                intensity_dict[freq] = [roi_total_intensity]
            processed_frames += 1
            print(f"프레임 #{frame_num} 처리: MW freq = {freq/1e9:.3f} GHz, ROI 총 intensity = {roi_total_intensity}")
            if processed_frames % 500 == 0:
                print(f"DEBUG: 소비된 고유 프레임 수 {processed_frames}, 현재 intensity_dict 항목 수: {len(intensity_dict)}")
        except queue.Empty:
            continue
    measurement_complete = True
