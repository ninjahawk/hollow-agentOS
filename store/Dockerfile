FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic
COPY store/server.py store/models.py ./store/
COPY store/server.py .
ENV HOLLOW_STORE_DATA=/store/data
ENV HOLLOW_STORE_PORT=7779
EXPOSE 7779
CMD ["python", "server.py"]
