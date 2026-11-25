FROM python:3.12-alpine
WORKDIR /app
COPY relay.py .
ENV PYTHONUNBUFFERED=1 STATE_FILE=/data/sent.json PORT=8080
EXPOSE 8080
CMD ["python", "relay.py"]
