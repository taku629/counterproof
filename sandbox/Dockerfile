FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY stress.py /opt/stress.py
# no secrets, no network tools; runs as nobody
USER 65534:65534
ENTRYPOINT ["python3", "/opt/stress.py"]
