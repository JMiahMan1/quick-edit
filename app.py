from flask import Flask, request, render_template, send_from_directory, flash, redirect, url_for, jsonify
import os
import yt_dlp
from werkzeug.utils import secure_filename
import json
import traceback
import re
from moviepy.editor import VideoFileClip
from celery_config import celery
from process_video import start_analysis_task, process_video_segments, process_and_concatenate_segments

UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'results'
THUMBNAIL_FOLDER = 'static/thumbnails'
CONFIG_FOLDER = 'config'
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESULTS_FOLDER'] = RESULTS_FOLDER
app.config['SECRET_KEY'] = 'a-very-secret-key-change-me'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)
os.makedirs(CONFIG_FOLDER, exist_ok=True)

CHANNEL_URL_FILE = os.path.join(CONFIG_FOLDER, 'channel_url.txt')

def get_youtube_video_id(url):
    """Extracts the YouTube video ID from various URL formats."""
    if not isinstance(url, str):
        return None
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        sensitivity = int(request.form.get('sensitivity', 80))
        
        submitted_url = request.form.get('url')

        if submitted_url:
            video_id = get_youtube_video_id(submitted_url)
            task = start_analysis_task.delay(
                sensitivity=sensitivity, 
                upload_dir=app.config['UPLOAD_FOLDER'], 
                thumbnail_dir=THUMBNAIL_FOLDER,
                url=submitted_url,
                youtube_video_id=video_id
            )
            return redirect(url_for('analysis_status', task_id=task.id))

        elif 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            if '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
                filename = secure_filename(file.filename)
                video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(video_path)
                
                task = start_analysis_task.delay(
                    sensitivity=sensitivity, 
                    thumbnail_dir=THUMBNAIL_FOLDER,
                    video_path=video_path,
                    upload_dir=app.config['UPLOAD_FOLDER']
                )
                return redirect(url_for('analysis_status', task_id=task.id))
            else:
                flash('Invalid file type.')
                return redirect(url_for('index'))
        else:
            flash('No file or URL provided.')
            return redirect(url_for('index'))

    # This part handles displaying the main page on a GET request
    saved_channel_url = ""
    if os.path.exists(CHANNEL_URL_FILE):
        with open(CHANNEL_URL_FILE, 'r') as f:
            saved_channel_url = f.read().strip()

    latest_videos = []
    if saved_channel_url:
        try:
            print(f"Fetching latest videos from channel's /streams endpoint: {saved_channel_url}")
            ydl_opts = {
                'playlistend': 5,
                'quiet': True,
                'ignoreerrors': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"{saved_channel_url}/streams", download=False)
                
                if 'entries' in result and result['entries'] is not None:
                    for entry in result['entries']:
                        if entry and entry.get('duration'):
                            latest_videos.append({
                                'title': entry.get('title'),
                                'url': entry.get('original_url') or f"https://www.youtube.com/watch?v={entry.get('id')}",
                                'thumbnail': entry.get('thumbnail')
                            })
                        if len(latest_videos) == 3:
                            break
        except Exception as e:
            flash(f"Could not fetch videos from the channel URL. Error: {str(e)}")

    return render_template('upload.html', saved_channel_url=saved_channel_url, latest_videos=latest_videos)

@app.route('/save_channel', methods=['POST'])
def save_channel():
    channel_url = request.form.get('channel_url', '')
    with open(CHANNEL_URL_FILE, 'w') as f:
        f.write(channel_url)
    flash("Channel URL saved successfully!")
    return redirect(url_for('index'))

@app.route('/analysis/<task_id>')
def analysis_status(task_id):
    return render_template('analysis.html', task_id=task_id)

@app.route('/preview/<task_id>')
def preview(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'SUCCESS':
        analysis_results = task.info.get('result', {})
        # This is now redundant as the ID is passed directly from the task result,
        # but it's a good failsafe.
        if 'youtube_video_id' not in analysis_results or not analysis_results['youtube_video_id']:
             original_url = analysis_results.get('original_url', '')
             video_id = get_youtube_video_id(original_url)
             analysis_results['youtube_video_id'] = video_id

        return render_template('preview.html', **analysis_results)
    else:
        flash("Analysis failed, is not ready, or the result expired.")
        return redirect(url_for('index'))


@app.route('/process', methods=['POST'])
def process_video_route():
    try:
        video_path = request.form['video_path']
        video_duration = float(request.form['video_duration'])
        
        override_hr_str = request.form.get('override_hr')
        override_min_str = request.form.get('override_min')
        override_sec_str = request.form.get('override_sec')
        
        is_override = any([
            override_hr_str.strip(),
            override_min_str.strip(),
            override_sec_str.strip()
        ])

        jobs = []

        if is_override:
            try:
                hr = int(override_hr_str or 0)
                m = int(override_min_str or 0)
                s = int(override_sec_str or 0)
                override_time = float(hr * 3600 + m * 60 + s)

                if not (0 < override_time < video_duration):
                    raise ValueError("Override time must be within video duration.")
                
                formats_1 = [f for f in ['mp4','mp3','txt'] if request.form.get(f'override_1_format_{f}')]
                if formats_1: jobs.append({"start": 0, "end": override_time, "formats": formats_1})
                
                formats_2 = [f for f in ['mp4','mp3','txt'] if request.form.get(f'override_2_format_{f}')]
                if formats_2: jobs.append({"start": override_time, "end": video_duration, "formats": formats_2})

            except (ValueError, TypeError) as e:
                return jsonify({'error': f'Invalid override time: {e}'}), 400
        else:
            num_segments = int(request.form['num_segments'])
            for i in range(num_segments):
                if request.form.get(f'segment_{i}_process'):
                    start_time = float(request.form[f'segment_{i}_start'])
                    end_time = float(request.form[f'segment_{i}_end'])
                    formats = [f for f in ['mp4','mp3','txt'] if request.form.get(f'segment_{i}_format_{f}')]
                    if formats: jobs.append({"start": start_time, "end": end_time, "formats": formats})

        if not jobs:
            return jsonify({'error': 'No segments or formats were selected for processing.'}), 400

        should_concatenate = request.form.get('concatenate_segments')
        
        if should_concatenate:
            task = process_and_concatenate_segments.delay(video_path, jobs, app.config['RESULTS_FOLDER'])
        else:
            task = process_video_segments.delay(video_path, jobs, app.config['RESULTS_FOLDER'])
        
        return jsonify({'task_id': task.id})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'An error occurred: {e}'}), 500


@app.route('/status/<task_id>')
def task_status(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': task.state, 'status': 'Pending...'}
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', '') if isinstance(task.info, dict) else str(task.info)
        }
        if task.state == 'SUCCESS':
            response['result'] = task.info.get('result', {})
    else:
        response = {
            'state': task.state,
            'status': str(task.info),
        }
    return jsonify(response)


@app.route('/results_page/<task_id>')
def results_page(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'SUCCESS':
        output_files = task.info.get('result', {})
        return render_template('results.html', output_files=output_files)
    else:
        flash("Task failed, is not ready, or the result expired.")
        return redirect(url_for('index'))


@app.route('/results/<filename>')
def download_file(filename):
    return send_from_directory(app.config["RESULTS_FOLDER"], filename, as_attachment=True)
