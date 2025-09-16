# Intelligent Video Splitting Web App

This is a Python-based web application that automates the process of splitting a video based on the appearance of a specific, user-defined scene. It uses computer vision to find the first high-confidence match to a template image and then presents a web interface to process the video into various formats. The application is containerized with Docker and uses Celery for robust background processing.

---

## ✨ Features

- **Web Interface**  
  A user-friendly interface for uploading videos or pasting links from sites like YouTube.

- **Specific Scene Detection**  
  Uses OpenCV feature matching to find the first instance of a specific scene from a provided `template.jpg` image.

- **Adjustable Sensitivity**  
  The threshold for what constitutes a "match" can be set directly in the UI.

- **Interactive Preview**  
  Displays thumbnails of the proposed video segments before processing.

- **Selective Processing**  
  Users can choose which segments to process and in what format.

- **Multiple Output Formats**  
  - Video only (MP4)  
  - Audio only (MP3)  
  - AI-generated transcription (TXT)  

- **Manual Override**  
  Option to ignore detection and split the video at a specific time.

- **Asynchronous Processing**  
  Uses Celery and Redis to handle heavy video processing in the background, keeping the UI fast and responsive.

- **Containerized**  
  Fully containerized with Docker and Docker Compose for easy setup and deployment.

---

## 📂 Project Structure

The application requires a specific file structure to run correctly. You must provide the `template.jpg` file.

```
/video-editor-app
|
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
|-- template.jpg         <-- Place your target image here
|-- app.py
|-- process_video.py
|
|-- /templates
|   |-- upload.html
|   |-- analysis.html
|   |-- preview.html
|   |-- results.html
|
|-- /static/
|-- /uploads/
|-- /results/
```

---

## ⚙️ Setup and Installation

### 1. Prerequisites
- Install **Docker Desktop** and ensure it is running.

### 2. Configuration
- Place the image you want the application to find in the root of the project directory.  
- Rename this image file to exactly `template.jpg`.

### 3. Build and Run
Open a terminal or command prompt and navigate to the root of the project folder (`/video-editor-app`).

Run the following command to build the Docker containers and start the application in the background:

```bash
docker-compose up --build -d
```

The application will be available in your browser at:  
👉 [http://localhost:5000](http://localhost:5000)

---

## 🚀 Usage

1. **Analyze Video**  
   Open the app in your browser. Upload a video file or paste a URL.  
   Adjust the *Feature Match Sensitivity* (a higher number requires a stricter match) and click **Analyze Video**.

2. **Wait for Analysis**  
   A waiting page will display while the video is analyzed in the background.  
   Once complete, you’ll be automatically redirected.

3. **Preview and Select**  
   The preview page will show the video split into segments based on the first detected match.  
   - By default, both segments are selected.  
   - You can uncheck segments or change their output formats (MP4, MP3, TXT).  

4. **Manual Override**  
   To ignore detection, enter a time (`HH:MM:SS`) in the override section and choose the output formats for the resulting parts.

5. **Concatenate Option**  
   Check **Combine all selected segments** if you want a single output file instead of multiple ones.

6. **Process Segments**  
   Click **Process Selected Segments**. A loading screen will show real-time task status.

7. **Download Results**  
   Once processing is complete, download links for your files will appear.

---

## 🛠️ Technology Stack

- **Backend:** Python, Flask  
- **Video/Image Processing:** OpenCV, FFmpeg  
- **Audio Transcription:** OpenAI Whisper  
- **Background Tasks:** Celery, Redis  
- **Containerization:** Docker, Docker Compose  

---
