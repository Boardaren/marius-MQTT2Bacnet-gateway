🏔️ Siemens BACnet MQTT Gateway
This is a professional, bidirectional gateway that connects MQTT sensors and actuators to a BACnet/IP network. The gateway is designed with a modern, Siemens-inspired web interface for easy configuration and maintenance.

✨ Features
Bidirectional Communication: Read data from MQTT to BACnet, and write setpoints from BACnet back to MQTT.
Dynamic Mapping: Automatically discovers new MQTT topics and creates BACnet points in real-time.
CSV Management: Download a mapping table, assign custom names to your variables, and set permissions (Read Only / Write).
Siemens Web UI: A simple interface for changing Device ID, network interface (NIC), and MQTT settings.
Docker-ready: Runs in a lightweight container.
🚀 Quick Start for Colleagues
1. Prerequisites
You must have Docker and Docker Compose installed on your server.

2. Download the Project
git clone https://github.com/Boardaren/marius-MQTT2Bacnet-gateway.git
cd marius-MQTT2Bacnet-gateway
