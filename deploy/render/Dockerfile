FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
RUN pip install --no-cache-dir aiohttp==3.10.5
WORKDIR /app
COPY app.py .
ENV PORT=8765
CMD ["python", "app.py"]
