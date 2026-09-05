FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
# В образе браузеры уже есть; пакет ставим явно под тот же python3, которым запускаем.
RUN python3 -m pip install --no-cache-dir aiohttp==3.10.5 playwright==1.47.0
WORKDIR /app
COPY app.py .
ENV PORT=8765
CMD ["python3", "app.py"]
