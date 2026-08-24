import BAC0
from BAC0.core.devices.local.factory import analog_value
import paho.mqtt.client as mqtt
import asyncio
import json
import os
import netifaces
from flask import Flask, render_template, request, send_file, redirect
import threading
import pandas as pd

# --- FILSTIER ---
CONFIG_FILE = "data/config.json"
MAPPING_FILE = "data/mapping.csv"

# --- LAST / LAG KONFIGURASJON ---
default_config = {
    "bacnet_id": 2001,
    "bacnet_iface": "ens18",
    "prefix": "S_",
    "mqtt_broker": "192.168.55.199",
    "mqtt_port": 1883,
    "mqtt_user": "Marius",
    "mqtt_pass": "Surfin1234!!"
}

if not os.path.exists(CONFIG_FILE):
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_FILE, 'w') as f: json.dump(default_config, f)

with open(CONFIG_FILE) as f:
    config = json.load(f)

# Globale variabler
bacnet = None
mqtt_client = None
loop = None
objects_map = {} # topic -> bacnet_name
last_known_vals = {} # bacnet_name -> value (for å oppdage endringer fra BACnet)
mapping_df = pd.DataFrame()
restart_requested = False

# --- HJELPEFUNKSJONER ---
def load_mapping():
    global mapping_df
    if os.path.exists(MAPPING_FILE):
        mapping_df = pd.read_csv(MAPPING_FILE)
        for col in ['read_only', 'write_enabled']:
            if col not in mapping_df.columns:
                mapping_df[col] = (col == 'read_only')
    else:
        mapping_df = pd.DataFrame(columns=['mqtt_topic', 'auto_name', 'user_name', 'instance', 'read_only', 'write_enabled'])

def save_mapping():
    mapping_df.to_csv(MAPPING_FILE, index=False)

def extract_value(payload):
    try: return float(payload)
    except:
        try:
            data = json.loads(payload)
            for key in ['temperature', 'humidity', 'pressure', 'power', 'energy', 'value', 'state', 'setpoint']:
                if key in data and isinstance(data[key], (int, float)): return float(data[key])
        except: pass
    return None

# --- BACNET & MQTT LOGIKK ---
def process_message(topic, payload_str):
    global mapping_df, objects_map, last_known_vals
    if not bacnet: return
    if "/config" in topic or "bridge" in topic: return
    
    val = extract_value(payload_str)
    if val is None: return

    try:
        # 1. Registrer ny topic i CSV hvis den mangler
        if topic not in mapping_df['mqtt_topic'].values:
            instance = len(mapping_df) + 10
            auto_name = f"{config['prefix']}{instance}_{topic.split('/')[-1]}"[:30]
            new_row = pd.DataFrame([{'mqtt_topic': topic, 'auto_name': auto_name, 'user_name': '', 'instance': instance, 'read_only': True, 'write_enabled': False}])
            mapping_df = pd.concat([mapping_df, new_row], ignore_index=True)
            save_mapping()
            print(f"✨ Ny topic lagt til i CSV: {topic}")

        # 2. Finn navn og rettigheter
        row = mapping_df[mapping_df['mqtt_topic'] == topic].iloc[0]
        u_name = str(row['user_name']).strip()
        final_name = u_name if u_name and u_name != "nan" else str(row['auto_name'])
        final_name = final_name[:30]
        instance = int(row['instance'])

        # 3. Opprett objektet hvis det ikke finnes i BACnet-stakken
        if topic not in objects_map:
            print(f"🏗️  Oppretter BACnet objekt: {final_name} (AV:{instance})")
            new_obj_model = analog_value(instance=instance, name=final_name, presentValue=val)
            new_obj_model.add_objects_to_application(bacnet)
            objects_map[topic] = final_name
            last_known_vals[final_name] = val
        else:
            # 4. Oppdater verdi fra MQTT til BACnet
            bacnet[final_name].presentValue = val
            last_known_vals[final_name] = val # Oppdater "siste kjente" så vi ikke trigger re-publish
            
    except Exception as e:
        print(f"❌ BACnet feil: {e}")

# --- WEBSERVER ---
app = Flask(__name__)

@app.route('/')
def index():
    ifaces = netifaces.interfaces()
    return render_template('index.html', config=config, ifaces=ifaces, mapping=mapping_df.to_dict('records'))

@app.route('/save_config', methods=['POST'])
def save_cfg():
    global config
    for key in config.keys():
        if key in request.form:
            val = request.form[key]
            if key in ['bacnet_id', 'mqtt_port']: val = int(val)
            config[key] = val
    with open(CONFIG_FILE, 'w') as f: json.dump(config, f)
    return redirect('/')

@app.route('/restart_gateway', methods=['POST'])
def restart():
    global restart_requested
    restart_requested = True
    return "<h1>Gateway restarter...</h1><script>setTimeout(function(){window.location.href='/'}, 5000);</script>"

@app.route('/download_csv')
def download():
    return send_file(MAPPING_FILE, as_attachment=True)

@app.route('/upload_csv', methods=['POST'])
def upload():
    if 'file' in request.files:
        request.files['file'].save(MAPPING_FILE)
        load_mapping()
    return redirect('/')

# --- HOVED-LOOP ---
async def gateway_loop():
    global bacnet, mqtt_client, objects_map, last_known_vals, restart_requested
    
    while True:
        objects_map = {}
        last_known_vals = {}
        restart_requested = False
        
        try:
            iface = config.get('bacnet_iface', 'ens18')
            ip = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]['addr']
            print(f"🚀 Starter BACnet på {iface} ({ip})")
            bacnet = BAC0.lite(ip=f"{ip}/24", deviceId=int(config['bacnet_id']))
            
            mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            if config['mqtt_user']: mqtt_client.username_pw_set(config['mqtt_user'], config['mqtt_pass'])
            mqtt_client.on_connect = lambda c, u, f, rc, p=None: c.subscribe("#")
            mqtt_client.on_message = lambda c, u, msg: loop.call_soon_threadsafe(process_message, msg.topic, msg.payload.decode())
            mqtt_client.connect(config['mqtt_broker'], int(config['mqtt_port']), 60)
            mqtt_client.loop_start()
            
            print("✅ Alt er tilkoblet. Venter på data...")
            
            while not restart_requested:
                # OVERVÅK ENDRINGER FRA BACNET (Toveis)
                for topic, b_name in list(objects_map.items()):
                    try:
                        current_val = bacnet[b_name].presentValue
                        if current_val != last_known_vals.get(b_name):
                            # Sjekk om skriving er tillatt i mapping_df
                            row = mapping_df[mapping_df['mqtt_topic'] == topic].iloc[0]
                            if bool(row['write_enabled']):
                                print(f"📡 BACnet -> MQTT: {topic} = {current_val}")
                                mqtt_client.publish(topic, str(current_val), retain=True)
                                last_known_vals[b_name] = current_val
                            else:
                                # Reset verdien hvis skriving ikke er tillatt
                                bacnet[b_name].presentValue = last_known_vals[b_name]
                    except: pass
                await asyncio.sleep(1)
            
            print("🔄 Restarter gateway...")
            mqtt_client.loop_stop()
            bacnet = None
            
        except Exception as e:
            print(f"❌ Kritisk feil: {e}")
            await asyncio.sleep(10)

async def main():
    global loop
    loop = asyncio.get_running_loop()
    load_mapping()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    await gateway_loop()

if __name__ == "__main__":
    asyncio.run(main())
