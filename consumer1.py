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
