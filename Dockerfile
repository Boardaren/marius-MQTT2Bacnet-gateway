FROM python:3.9-slim
RUN apt-get update && apt-get install -y libcap2-bin && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir BAC0 bacpypes3 paho-mqtt flask netifaces pandas
WORKDIR /app
COPY . .
CMD ["python", "server.py"]
