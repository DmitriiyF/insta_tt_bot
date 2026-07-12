FROM python:3.11-slim
WORKDIR /app
# Устанавливаем системный ffmpeg для звука
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]