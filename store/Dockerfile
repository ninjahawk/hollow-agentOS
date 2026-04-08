FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx
COPY store/server.py .
COPY store/models.py .
ENV HOLLOW_STORE_DATA=/store/data
ENV HOLLOW_STORE_PORT=7779
EXPOSE 7779
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:7779/health')"
CMD ["python", "server.py"]
