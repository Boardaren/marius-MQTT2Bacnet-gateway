# Siemens-Style MQTT ↔ BACnet Gateway (v2)

A professional, bidirectional gateway that connects MQTT sensors and actuators (e.g. Zigbee2MQTT, Home Assistant, Node-RED, Shelly) to a BACnet/IP network. Built with [BAC0](https://bac0.readthedocs.io/) and `bacpypes3`, and designed to run as a lightweight Docker container.

Originally built for a home automation setup (Zigbee2MQTT + Home Assistant) that needed to expose sensor data to a Siemens Desigo Optic BMS via standard BACnet/IP, without requiring a full building automation controller.

## Features

- **Dynamic MQTT discovery** — new MQTT topics are automatically detected and assigned a permanent BACnet Analog Value object with a fixed instance number. IDs never change once assigned, even after a restart.
- **Bidirectional communication** — read values from MQTT into BACnet, and (optionally) write BACnet commands back out to MQTT.
- **BACnet priority writing (1–16)** — every point writes to a configurable priority slot (default 16), so it plays nicely alongside other BACnet controllers on the same network.
- **Alarming / Intrinsic Reporting** — per-point configurable High/Low limits, enable/disable, alarm type (Simple/Basic/Extended) and classification (Urgent/High/Medium/Low), mapped to proper BACnet Notification Class objects.
- **Live status** — `Event_State` and `Status_Flags` update correctly on the BACnet object itself when a limit is exceeded, so any standards-compliant BACnet client (Desigo Optic, YABE, etc.) can see the alarm state via normal property reads or COV subscriptions.
- **Disturbance (Dstb) objects** — a dedicated Binary Value object per alarmed point, mirroring its alarm state — handy for simple graphics/dashboards that just need a boolean.
- **CSV-driven configuration** — every point's name, BACnet instance, priority, and alarm settings are stored in a downloadable/uploadable `mapping.csv` file. No code changes needed to rename points or tune alarm limits.
- **Configurable BACnet port and Device ID** — set via the web UI, no need to touch the Dockerfile.
- **Automatic nightly restart** — if new devices were discovered during the day, the gateway schedules a clean restart at 03:00 to lock everything in (manual restart also available at any time).
- **Web UI** — Siemens-styled dashboard with live system and MQTT traffic logs (downloadable), CSV upload/download, and a `/debug` endpoint for troubleshooting the underlying `bacpypes3` application.

## Architecture

```mermaid
flowchart LR
    MQTT[MQTT Broker<br/>Zigbee2MQTT / Home Assistant / Node-RED] -->|subscribe #| GW[Gateway<br/>Python / BAC0]
    GW -->|creates/updates| AV[BACnet Analog Value objects]
    GW -->|creates| NC[Notification Class objects<br/>11-43]
    GW -->|creates| BV[Disturbance BV objects]
    AV -->|Event_State / Status_Flags| BMS[BACnet Client<br/>Desigo Optic / YABE]
    GW -->|serves| WEB[Web UI :5000]
    WEB -->|reads/writes| CSV[mapping.csv]
