FROM python:3.9-slim
RUN apt-get update && apt-get install -y libcap2-bin
RUN pip install BAC0 paho-mqtt flask netifaces pandas
WORKDIR /app
COPY . .
CMD ["python", "server.py"]
