import cv2
import numpy as np
from moviepy.editor import VideoFileClip
import os
import shutil
import whisper
import yt_dlp
from celery import Celery
import traceback

# --- Celery Configuration ---
celery = Celery(
    'tasks',
    broker='redis://redis:6379/0',
    backend='redis://redis:6379/0'
)
celery.conf.update(task_track_started=True)


# --- Celery Tasks ---

@celery.task(bind=True)
def start_analysis_task(self, sensitivity, thumbnail_dir, upload_dir, video_path=None, url=None):
    """
    The new entry point task. Handles download (if URL provided) and then analysis.
    """
    try:
        # --- Download Logic ---
        if url:
            self.update_state(state='PROGRESS', meta={'status': 'Downloading video...'})
            ydl_opts = { 'format': 'best[ext=mp4]/best', 'outtmpl': os.path.join(upload_dir, '%(title)s.%(ext)s') }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                video_path = ydl.prepare_filename(info_dict)
        
        if not video_path or not os.path.exists(video_path):
            raise FileNotFoundError("Video file not found after download/upload.")

        # --- Analysis Logic ---
        self.update_state(state='PROGRESS', meta={'status': 'Analyzing video for template match...'})
        
        clip = VideoFileClip(video_path)
        video_duration = clip.duration
        clip.close()

        detected_points = analyze_video_for_changes(video_path, sensitivity=sensitivity)
        all_points = sorted(list(set([0] + detected_points + [video_duration])))

        segments = []
        total_segments = len(all_points) - 1
        for i in range(total_segments):
            self.update_state(state='PROGRESS', meta={'status': f'Generating thumbnail {i+1} of {total_segments}...'})
            start = all_points[i]
            end = all_points[i+1]
            if end > start:
                thumbnail_url = extract_frame_as_jpeg(video_path, start, thumbnail_dir)
                segments.append({
                    "index": i, "start": start, "end": end, "thumbnail": thumbnail_url
                })

        result_data = {
            'video_path': video_path, 'video_duration': video_duration, 'segments': segments
        }
        
        return {'status': 'Analysis Complete', 'result': result_data}

    except Exception as e:
        # Make sure to set task to failure state with the error message
        self.update_state(state='FAILURE', meta={'status': f'An error occurred: {str(e)}'})
        # Re-raise the exception so Celery logs it
        raise e


@celery.task(bind=True)
def process_video_segments(self, video_path, jobs, output_dir):
    """
    Celery task to process a list of jobs, creating a separate file for each segment.
    """
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    total_jobs = len(jobs)
    
    try:
        clip = VideoFileClip(video_path)
        has_audio = clip.audio is not None
        clip.close()
    except Exception:
        has_audio = False

    output_files = {'video': [], 'audio': [], 'text': []}

    for i, job in enumerate(jobs):
        self.update_state(state='PROGRESS', meta={'status': f'Processing segment {i+1} of {total_jobs}...'})
        start_time = job['start']
        end_time = job['end']
        formats = job['formats']
        
        if end_time > start_time:
            segment_name = f"{base_name}_segment_at_{int(start_time)}s"
            segment_video_path = os.path.join(output_dir, f"{segment_name}.mp4")
            segment_audio_path = os.path.join(output_dir, f"{segment_name}.mp3")
            segment_text_path = os.path.join(output_dir, f"{segment_name}.txt")

            try:
                duration = end_time - start_time
                cmd_video = (f'ffmpeg -y -ss {start_time} -i "{video_path}" -t {duration} '
                             f'-c:v libx264 -c:a aac "{segment_video_path}"')
                
                needs_mp4 = 'mp4' in formats
                needs_audio_file = 'mp3' in formats or 'txt' in formats
                
                if needs_mp4:
                    os.system(cmd_video)
                    if os.path.exists(segment_video_path):
                        output_files['video'].append(os.path.basename(segment_video_path))
                
                if has_audio and needs_audio_file:
                    audio_source_path = segment_video_path
                    if not needs_mp4:
                        audio_source_path = os.path.join(output_dir, f"temp_audio_{i+1}.mp3")
                        cmd_temp_audio = f'ffmpeg -y -ss {start_time} -i "{video_path}" -t {duration} -vn -q:a 0 "{audio_source_path}"'
                        os.system(cmd_temp_audio)

                    if os.path.exists(audio_source_path):
                        if 'mp3' in formats:
                            if not needs_mp4:
                                shutil.move(audio_source_path, segment_audio_path)
                            else:
                                cmd_audio_extract = f'ffmpeg -y -i "{audio_source_path}" -vn -q:a 0 "{segment_audio_path}"'
                                os.system(cmd_audio_extract)
                            
                            if os.path.exists(segment_audio_path):
                                output_files['audio'].append(os.path.basename(segment_audio_path))
                        
                        if 'txt' in formats:
                            transcription_source = segment_audio_path if 'mp3' in formats and os.path.exists(segment_audio_path) else audio_source_path
                            transcribed_text = transcribe_audio(transcription_source)
                            with open(segment_text_path, 'w', encoding='utf-8') as f:
                                f.write(transcribed_text)
                            if os.path.exists(segment_text_path):
                                output_files['text'].append(os.path.basename(segment_text_path))

                        if not needs_mp4 and os.path.exists(audio_source_path):
                            os.remove(audio_source_path)
            except Exception as e:
                print(f"ERROR processing segment: {e}")
    
    if os.path.exists(video_path):
        os.remove(video_path)

    return {'status': 'Task complete!', 'result': output_files}


@celery.task(bind=True)
def process_and_concatenate_segments(self, video_path, jobs, output_dir):
    """
    Celery task to create and concatenate segments into single files.
    """
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_files = {'video': [], 'audio': [], 'text': []}
    temp_video_files = []
    total_jobs = len(jobs)
    
    self.update_state(state='PROGRESS', meta={'status': 'Creating temporary segments...'})
    for i, job in enumerate(jobs):
        self.update_state(state='PROGRESS', meta={'status': f'Creating temporary segment {i+1} of {total_jobs}...'})
        start_time = job['start']
        end_time = job['end']
        
        if end_time > start_time:
            temp_segment_path = os.path.join(output_dir, f"temp_concat_{i+1}.mp4")
            duration = end_time - start_time
            cmd = (f'ffmpeg -y -ss {start_time} -i "{video_path}" -t {duration} '
                   f'-c:v libx264 -c:a aac "{temp_segment_path}"')
            os.system(cmd)
            if os.path.exists(temp_segment_path):
                temp_video_files.append(temp_segment_path)
    
    if not temp_video_files:
        return {'status': 'Task failed: No temporary segments created.', 'result': output_files}

    concat_list_path = os.path.join(output_dir, "concat_list.txt")
    with open(concat_list_path, 'w') as f:
        for filename in temp_video_files:
            f.write(f"file '{os.path.basename(filename)}'\n")

    needed_formats = set(fmt for job in jobs for fmt in job['formats'])
            
    final_video_path = os.path.join(output_dir, f"{base_name}_combined.mp4")
    final_audio_path = os.path.join(output_dir, f"{base_name}_combined.mp3")
    final_text_path = os.path.join(output_dir, f"{base_name}_combined.txt")
    
    try:
        if any(f in needed_formats for f in ['mp4', 'mp3', 'txt']):
            self.update_state(state='PROGRESS', meta={'status': 'Stitching segments together...'})
            cmd_concat = f'ffmpeg -y -f concat -safe 0 -i "{concat_list_path}" -c copy "{final_video_path}"'
            os.system(cmd_concat)
            if os.path.exists(final_video_path):
                output_files['video'].append(os.path.basename(final_video_path))
        
        if 'mp3' in needed_formats and os.path.exists(final_video_path):
            self.update_state(state='PROGRESS', meta={'status': 'Extracting combined audio...'})
            cmd_audio = f'ffmpeg -y -i "{final_video_path}" -vn -q:a 0 "{final_audio_path}"'
            os.system(cmd_audio)
            if os.path.exists(final_audio_path):
                output_files['audio'].append(os.path.basename(final_audio_path))
        
        if 'txt' in needed_formats and os.path.exists(final_audio_path):
            self.update_state(state='PROGRESS', meta={'status': 'Transcribing combined audio...'})
            transcribed_text = transcribe_audio(final_audio_path)
            with open(final_text_path, 'w', encoding='utf-8') as f:
                f.write(transcribed_text)
            if os.path.exists(final_text_path):
                output_files['text'].append(os.path.basename(final_text_path))
    finally:
        self.update_state(state='PROGRESS', meta={'status': 'Cleaning up temporary files...'})
        if os.path.exists(concat_list_path): os.remove(concat_list_path)
        for filename in temp_video_files:
            if os.path.exists(filename): os.remove(filename)
        if os.path.exists(video_path): os.remove(video_path)

    return {'status': 'Task complete!', 'result': output_files}


# --- Helper Functions (Not Celery Tasks) ---

def extract_frame_as_jpeg(video_path, time_in_seconds, output_dir):
    try:
        os.makedirs(output_dir, exist_ok=True)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        
        cap.set(cv2.CAP_PROP_POS_MSEC, time_in_seconds * 1000)
        ret, frame = cap.read()
        cap.release()
        
        if ret:
            safe_filename = os.path.basename(video_path).replace(" ", "_")
            thumbnail_filename = f"thumb_{safe_filename}_{int(time_in_seconds)}.jpg"
            thumbnail_path = os.path.join(output_dir, thumbnail_filename)
            cv2.imwrite(thumbnail_path, frame)
            return os.path.join('thumbnails', thumbnail_filename)
        return None
    except Exception as e:
        print(f"Error extracting frame: {e}")
        return None

def transcribe_audio(audio_path):
    print(f"Loading transcription model and transcribing {audio_path}...")
    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)
        return result["text"]
    except Exception as e:
        print(f"Error during transcription: {e}")
        return "Transcription failed."
def analyze_video_for_changes(video_path, sensitivity=5):
    """
    Analyzes video for the FIRST high-priority template match and stops.
    If no template match is found, it falls back to finding all general changes.
    """
    print("Analyzing video for template matches and other changes...")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    try:
        template = cv2.imread("template.jpg", 0)
        orb = cv2.ORB_create(nfeatures=1000)
        kp_template, des_template = orb.detectAndCompute(template, None)
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        template_available = True
        print("Template image loaded successfully.")
    except Exception as e:
        print(f"WARNING: Could not load template image. Error: {e}")
        template_available = False

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        fps = 30

    cooldown_frames = 2 * int(fps)
    last_cut_frame = -cooldown_frames
    frame_history = {}
    cut_points = []
    
    frame_num = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        if frame_num < last_cut_frame + cooldown_frames:
            frame_num += 1
            continue
        
        # --- MODIFIED LOGIC: Prioritize template match and stop if found ---
        if template_available:
            kp_frame, des_frame = orb.detectAndCompute(gray_frame, None)
            if des_frame is not None:
                matches = bf.knnMatch(des_template, des_frame, k=2)
                good_matches = [m for m_n in matches if len(m_n) == 2 for m, n in [m_n] if m.distance < 0.75 * n.distance]
                
                # Using a fixed, high threshold for a "definite" match
                if len(good_matches) > 80:
                    timestamp = frame_num / fps
                    print(f"High-priority template match found at {timestamp:.2f}s with {len(good_matches)} features. Stopping analysis.")
                    cut_points.append(timestamp)
                    break # <-- STOPS THE LOOP

        # This part only runs if no template match was found in the frame
        current_hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        cv2.normalize(current_hist, current_hist).flatten()
        current_brightness = np.mean(gray_frame)
        
        frame_history[frame_num] = {'brightness': current_brightness, 'hist': current_hist}
        
        past_frame_num = frame_num - int(fps)
        if past_frame_num in frame_history:
            last_frame_data = frame_history[past_frame_num]
            
            brightness_diff = current_brightness - last_frame_data['brightness']
            scene_diff = cv2.compareHist(current_hist, last_frame_data['hist'], cv2.HISTCMP_CHISQR_ALT)
            
            norm_brightness_score = min(brightness_diff / 50.0, 1.0) if brightness_diff > 0 else 0
            norm_scene_score = min(scene_diff / 1.0, 1.0)
            
            combined_score = (norm_brightness_score * 0.4) + (norm_scene_score * 0.6)
            final_rating = int(round(combined_score * 10))

            if final_rating >= sensitivity:
                timestamp = frame_num / fps
                print(f"General change detected at {timestamp:.2f}s. (Rating: {final_rating}/10)")
                cut_points.append(timestamp)
                last_cut_frame = frame_num

        frame_num += 1

    cap.release()
    print(f"Analysis complete. Found {len(cut_points)} total change events.")
    return sorted(list(set(cut_points)))
