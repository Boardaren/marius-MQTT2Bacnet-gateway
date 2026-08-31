import BAC0
from BAC0.core.devices.local.factory import analog_value, binary_value
import paho.mqtt.client as mqtt
import asyncio
import json
import os
import netifaces
import time
import re
from flask import Flask, render_template, request, send_file, redirect, jsonify, Response
import threading
import pandas as pd
from collections import deque
from datetime import datetime

# --- FILE PATHS ---
CONFIG_FILE = "data/config.json"
MAPPING_FILE = "data/mapping.csv"

# --- GLOBAL STATE ---
log_buffer = deque(maxlen=300)
mqtt_log_buffer = deque(maxlen=300)
new_devices_detected = False
restart_requested = False

def log_event(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    log_buffer.append(full_msg)

def log_mqtt(topic, payload):
    timestamp = datetime.now().strftime("%H:%M:%S")
    short_payload = (payload[:75] + '..') if len(payload) > 75 else payload
    mqtt_log_buffer.append(f"[{timestamp}] {topic} -> {short_payload}")

def clean_topic(t):
    """Strips everything except letters/digits for robust CSV topic matching."""
    return re.sub(r'[^a-zA-Z0-9]', '', str(t)).lower().strip()

def get_nc_id(classification, alarm_type):
    base_map = {"Urgent": 10, "High": 20, "Medium": 30, "Normal": 30, "Low": 40}
    type_map = {"Simple": 1, "Basic": 2, "Extended": 3}
    return base_map.get(str(classification), 30) + type_map.get(str(alarm_type), 2)

# --- NOTIFICATION CLASS SUPPORT ---
NotificationClassObject = None

def load_nc_support():
    global NotificationClassObject
    try:
        from bacpypes3.local.object import NotificationClassObject as NC1
        NotificationClassObject = NC1
        log_event("Import OK: bacpypes3.local.object.NotificationClassObject")
    except Exception as e1:
        log_event(f"Import failed (bacpypes3.local.object): {repr(e1)}")
        try:
            from bacpypes3.object import NotificationClassObject as NC2
            NotificationClassObject = NC2
            log_event("Import OK: bacpypes3.object.NotificationClassObject")
        except Exception as e2:
            log_event(f"Import also failed (bacpypes3.object): {repr(e2)}")

# --- CONFIGURATION ---
if not os.path.exists(CONFIG_FILE):
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({
            "bacnet_id": 2007,
            "bacnet_port": 47808,
            "bacnet_iface": "ens18",
            "prefix": "S_",
            "mqtt_broker": "192.168.55.199",
            "mqtt_port": 1883,
            "mqtt_user": "Marius",
            "mqtt_pass": "Surfin1234!!"
        }, f)
with open(CONFIG_FILE) as f:
    config = json.load(f)

bacnet = None
mqtt_client = None
loop = None
objects_dict = {}       # used ONLY as a "does this object already exist" marker
topic_to_name = {}      # cleaned_topic -> final_name
dstb_map = {}            # final_name -> {bv_name, low, high}
mapping_df = pd.DataFrame()

def load_mapping():
    global mapping_df
    if os.path.exists(MAPPING_FILE):
        try:
            df = None
            for sep in [';', ',']:
                try:
                    candidate = pd.read_csv(MAPPING_FILE, sep=sep, encoding='utf-8-sig')
                    candidate.columns = (
                        candidate.columns.str.strip()
                        .str.replace('ï»¿', '', regex=False)
                        .str.replace('\ufeff', '', regex=False)
                    )
                    if 'mqtt_topic' in candidate.columns:
                        df = candidate
                        break
                except Exception:
                    continue
            if df is None:
                raise ValueError("Could not find the 'mqtt_topic' column in the CSV file.")

            mapping_df = df
            mapping_df['mqtt_topic'] = mapping_df['mqtt_topic'].astype(str).str.strip()
            mapping_df.drop_duplicates(subset=['mqtt_topic'], keep='first', inplace=True)

            defaults = {
                'user_name': '', 'priority': 16, 'alarm_enable': False,
                'low_limit': 0.0, 'high_limit': 100.0,
                'alarm_type': 'Basic', 'classification': 'Medium'
            }
            for col, val in defaults.items():
                if col not in mapping_df.columns:
                    mapping_df[col] = val

            log_event(f"Loaded CSV with {len(mapping_df)} rows.")
        except Exception as e:
            log_event(f"CSV error: {e}")
            mapping_df = pd.DataFrame(columns=[
                'mqtt_topic', 'auto_name', 'user_name', 'instance', 'read_only',
                'write_enabled', 'priority', 'alarm_enable', 'low_limit',
                'high_limit', 'alarm_type', 'classification'
            ])
    else:
        mapping_df = pd.DataFrame(columns=[
            'mqtt_topic', 'auto_name', 'user_name', 'instance', 'read_only',
            'write_enabled', 'priority', 'alarm_enable', 'low_limit',
            'high_limit', 'alarm_type', 'classification'
        ])

def save_mapping():
    mapping_df.to_csv(MAPPING_FILE, index=False)

def extract_value(payload):
    try:
        return float(payload)
    except:
        try:
            data = json.loads(payload)
            for k in ['temperature', 'local_temperature', 'current_heating_setpoint',
                      'humidity', 'power', 'energy', 'value', 'state', 'illuminance', 'battery']:
                if k in data and isinstance(data[k], (int, float)):
                    return float(data[k])
        except:
            pass
    return None

def create_bacnet_object(row, val=0.0):
    global objects_dict, topic_to_name, dstb_map
    try:
        topic = str(row['mqtt_topic']).strip()
        u_name = str(row.get('user_name', '')).strip()
        final_name = u_name if u_name and u_name.lower() != "nan" else str(row['auto_name'])
        final_name = final_name[:30]
        instance = int(row['instance'])
        alarm_enabled = str(row.get('alarm_enable', False)).lower() in ['true', '1', 'yes']

        if final_name in objects_dict:
            return

        log_event(f"Creating: {final_name} (AV:{instance})")

        alarm_props = {
            'highLimit': float(row.get('high_limit', 100.0)),
            'lowLimit': float(row.get('low_limit', 0.0)),
            'limitEnable': [1, 1] if alarm_enabled else [0, 0],
            'eventEnable': [1, 1, 1] if alarm_enabled else [0, 0, 0],
            'timeDelay': 5
        }
        if alarm_enabled:
            alarm_props['notificationClass'] = get_nc_id(
                row.get('classification', 'Medium'), row.get('alarm_type', 'Basic')
            )

        new_av = analog_value(instance=instance, name=final_name, presentValue=val, properties=alarm_props)
        new_av.add_objects_to_application(bacnet)
        objects_dict[final_name] = True
        topic_to_name[clean_topic(topic)] = final_name

        if alarm_enabled:
            dstb_name = f"{final_name}_Dstb"[:30]
            new_bv = binary_value(instance=instance + 1000, name=dstb_name, presentValue=False)
            new_bv.add_objects_to_application(bacnet)
            dstb_map[final_name] = {
                "bv_name": dstb_name,
                "low": float(row.get('low_limit', 0.0)),
                "high": float(row.get('high_limit', 100.0))
            }
    except Exception as e:
        log_event(f"Could not create {row.get('mqtt_topic', '?')}: {e}")

def process_message(topic, payload_str):
    global mapping_df, objects_dict, topic_to_name, new_devices_detected
    log_mqtt(topic, payload_str)
    if not bacnet or "/config" in topic or "bridge" in topic:
        return

    val = extract_value(payload_str)
    if val is None:
        return

    topic_key = clean_topic(topic)

    # Object already active -> write via bacnet[name], NEVER via objects_dict
    if topic_key in topic_to_name:
        name = topic_to_name[topic_key]
        if name in objects_dict:
            try:
                row = mapping_df[mapping_df['mqtt_topic'].str.lower().str.strip() == topic.strip().lower()]
                prio = int(row.iloc[0].get('priority', 16)) if not row.empty else 16
                bacnet[name].presentValue = (val, prio)
            except Exception:
                try:
                    bacnet[name].presentValue = val
                except Exception as e2:
                    log_event(f"Could not write value to {name}: {e2}")
            return

    existing = mapping_df[mapping_df['mqtt_topic'].str.lower().str.strip() == topic.strip().lower()]
    if not existing.empty:
        create_bacnet_object(existing.iloc[0].to_dict(), val)
        return

    # Brand new device -> register in CSV with a permanent instance number
    instance = int(mapping_df['instance'].max() + 1) if not mapping_df.empty else 10
    auto_name = f"{config['prefix']}{instance}_{topic.split('/')[-1]}"[:30]
    new_row_dict = {
        'mqtt_topic': topic.strip(), 'auto_name': auto_name, 'user_name': '',
        'instance': instance, 'read_only': True, 'write_enabled': False,
        'priority': 16, 'alarm_enable': False, 'low_limit': 0.0,
        'high_limit': 100.0, 'alarm_type': 'Basic', 'classification': 'Medium'
    }
    new_row = pd.DataFrame([new_row_dict])
    mapping_df = pd.concat([mapping_df, new_row], ignore_index=True)
    save_mapping()
    new_devices_detected = True
    log_event(f"New device discovered: {topic} -> fixed ID {instance}")
    create_bacnet_object(new_row_dict, val)

def find_real_app(app_obj):
    """Finds the actual bacpypes3 application object that has 'add_object'."""
    if hasattr(app_obj, 'add_object'):
        return app_obj, "this_application (direct)"
    for attr in ['_app', 'app', 'application', '_this_application']:
        if hasattr(app_obj, attr):
            candidate = getattr(app_obj, attr)
            if hasattr(candidate, 'add_object'):
                return candidate, attr
    return None, None

# --- WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', config=config, ifaces=netifaces.interfaces(), mapping=mapping_df.to_dict('records'))

@app.route('/get_logs')
def get_logs():
    return jsonify({"system": list(log_buffer), "mqtt": list(mqtt_log_buffer)})

@app.route('/download_system_logs')
def download_system_logs():
    return Response("\n".join(list(log_buffer)), mimetype="text/plain",
                     headers={"Content-disposition": "attachment; filename=system_logs.txt"})

@app.route('/download_mqtt_logs')
def download_mqtt_logs():
    return Response("\n".join(list(mqtt_log_buffer)), mimetype="text/plain",
                     headers={"Content-disposition": "attachment; filename=mqtt_logs.txt"})

@app.route('/debug')
def debug_info():
    if not bacnet:
        return jsonify({"error": "BACnet has not started yet."})
    app_obj = bacnet.this_application
    info = {
        "this_application_type": str(type(app_obj)),
        "notification_class_object_available": NotificationClassObject is not None,
        "known_points": list(objects_dict.keys()),
    }
    real_app, path = find_real_app(app_obj)
    info["found_real_app_via"] = path
    return jsonify(info)

@app.route('/save_config', methods=['POST'])
def save_cfg():
    global config
    for key in config.keys():
        if key in request.form:
            val = request.form[key]
            if key in ['bacnet_id', 'bacnet_port', 'mqtt_port']:
                val = int(val)
            config[key] = val
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f)
    log_event("Configuration saved.")
    return redirect('/')

@app.route('/restart_gateway', methods=['POST'])
def restart():
    global restart_requested
    restart_requested = True
    log_event("Manual restart requested. Shutting down the process in a few seconds...")
    return "<h1>Gateway restarting (entire container)...</h1><script>setTimeout(function(){window.location.href='/'}, 8000);</script>"

@app.route('/download_csv')
def download():
    return send_file(MAPPING_FILE, as_attachment=True)

@app.route('/upload_csv', methods=['POST'])
def upload():
    if 'file' in request.files:
        request.files['file'].save(MAPPING_FILE)
        load_mapping()
        log_event("New CSV uploaded. Restart to lock in the changes.")
    return redirect('/')

async def gateway_loop():
    """
    IMPORTANT: The BACnet stack runs exactly ONCE per process. On restart
    (manual, scheduled at 03:00, or a critical error) the ENTIRE Python
    process exits via os._exit(). Docker's 'restart: always' policy then
    launches a completely fresh container, guaranteeing a clean BAC0 state
    and preventing BAC0's process-global object name/instance registry from
    causing false duplicate objects.
    """
    global bacnet, mqtt_client, dstb_map, objects_dict, topic_to_name, new_devices_detected, restart_requested
    try:
        log_event("Preparing BACnet stack...")
        await asyncio.sleep(2)

        iface = config.get('bacnet_iface', 'ens18')
        ip = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]['addr']
        port = int(config.get('bacnet_port', 47808))
        log_event(f"Starting BACnet on {ip}:{port} (Device ID: {config['bacnet_id']})")

        bacnet = BAC0.lite(ip=f"{ip}/24", port=port, deviceId=int(config['bacnet_id']))

        real_app, via = find_real_app(bacnet.this_application)
        if real_app:
            log_event(f"Found BACnet engine via: {via}")
        else:
            log_event("Could not find an 'add_object' method.")

        if real_app and NotificationClassObject:
            created = 0
            error_shown = False
            for nc_id in [11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43]:
                try:
                    nc_obj = NotificationClassObject(
                        objectIdentifier=('notification-class', nc_id),
                        objectName=f'NC_{nc_id}',
                        notificationClass=nc_id,
                        priority=[1, 1, 1],
                        ackRequired=[0, 0, 0],
                        recipientList=[]
                    )
                    real_app.add_object(nc_obj)
                    created += 1
                except Exception as e:
                    if not error_shown:
                        log_event(f"CAUSE of NC error: {repr(e)}")
                        error_shown = True
            if created:
                log_event(f"{created} Notification Class objects registered.")
        else:
            log_event("Skipping NC creation (NotificationClassObject not available).")

        log_event("Building object structure from CSV...")
        for _, row in mapping_df.iterrows():
            create_bacnet_object(row.to_dict())

        mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        if config.get('mqtt_user'):
            mqtt_client.username_pw_set(config['mqtt_user'], config['mqtt_pass'])
        mqtt_client.on_connect = lambda c, u, f, rc, p=None: c.subscribe("#")
        mqtt_client.on_message = lambda c, u, msg: loop.call_soon_threadsafe(
            process_message, msg.topic, msg.payload.decode(errors='ignore')
        )
        mqtt_client.connect(config['mqtt_broker'], int(config['mqtt_port']), 60)
        mqtt_client.loop_start()
        log_event("System is operational.")

        while not restart_requested:
            now = datetime.now()
            if now.hour == 3 and now.minute == 0 and new_devices_detected:
                log_event("03:00 and new devices found -> scheduled restart of the entire container.")
                restart_requested = True
                new_devices_detected = False

            for av_name, info in list(dstb_map.items()):
                try:
                    val = bacnet[av_name].presentValue
                    bv_name = info['bv_name']
                    is_alarm = (val < info['low'] or val > info['high'])
                    if bacnet[bv_name].presentValue != is_alarm:
                        bacnet[bv_name].presentValue = is_alarm
                        log_event(f"{av_name}: {'ALARM' if is_alarm else 'NORMAL'}")
                    new_state = 'high-limit' if val > info['high'] else ('low-limit' if val < info['low'] else 'normal')
                    if bacnet[av_name].eventState != new_state:
                        bacnet[av_name].eventState = new_state
                        bacnet[av_name].statusFlags = [1, 0, 0, 0] if is_alarm else [0, 0, 0, 0]
                except Exception:
                    pass
            await asyncio.sleep(1)

        log_event("Shutting down process for a clean restart via Docker...")
        try:
            mqtt_client.loop_stop()
            bacnet.disconnect()
        except Exception:
            pass
        await asyncio.sleep(2)
        os._exit(0)

    except Exception as e:
        log_event(f"Critical error: {e}. Shutting down process so Docker restarts it...")
        try:
            if bacnet:
                bacnet.disconnect()
        except Exception:
            pass
        await asyncio.sleep(2)
        os._exit(1)

async def main():
    global loop
    loop = asyncio.get_running_loop()
    load_mapping()
    load_nc_support()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    await gateway_loop()

if __name__ == "__main__":
    asyncio.run(main())
