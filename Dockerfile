# Python ka official image use karein
FROM python:3.10-slim

# System dependencies aur FFmpeg install karein
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Work directory set karein
WORKDIR /app

# Requirements copy aur install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki saara code copy karein
COPY . .

# Bot run karne ki command
CMD ["python", "hero.py"]
