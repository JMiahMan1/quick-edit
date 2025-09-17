# Use a more current Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies: redis for celery, ffmpeg for whisper, etc.
RUN apt-get update && apt-get install -y \
    redis-server \
    ffmpeg \
    build-essential \
    cargo \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the template image for scene matching
COPY template.jpg .

# Copy the rest of the application's code into the container
COPY . .

# Make port 5000 available to the world outside this container
EXPOSE 5000
