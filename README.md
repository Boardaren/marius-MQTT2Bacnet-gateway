# 🏔️ Siemens BACnet MQTT Gateway

Dette er en profesjonell, toveis gateway som kobler MQTT-sensorer og aktuatorer til et BACnet/IP-nettverk. Gatewayen er designet med et moderne Siemens-inspirert webgrensesnitt for enkel konfigurasjon og vedlikehold.

## ✨ Funksjoner
- **Toveis kommunikasjon:** Les data fra MQTT til BACnet, og skriv settpunkter fra BACnet tilbake til MQTT.
- **Dynamisk Mapping:** Oppdager nye MQTT-topics automatisk og lager BACnet-punkter i sanntid.
- **CSV-styring:** Last ned en mapping-tabell, gi variablene dine egne navn, og sett rettigheter (Read Only / Write).
- **Siemens Web UI:** Enkelt grensesnitt for å endre Device ID, nettverkskort og MQTT-innstillinger.
- **Docker-klar:** Kjører i en lettvekts container.

## 🚀 Hurtigstart for kollegaer

### 1. Forutsetninger
Du må ha **Docker** og **Docker Compose** installert på serveren din.

### 2. Last ned prosjektet
```bash
git clone https://github.com/Boardaren/marius-MQTT2Bacnet-gateway.git
cd marius-MQTT2Bacnet-gateway
