#!/usr/bin/env bash
# =============================================================================
# ATG P8 - Configuracao dos nos Meshtastic (Meshtastic Python CLI)
#
#   pip install meshtastic
#   ./hardware/meshtastic_cli_config.sh /dev/ttyUSB0 ATG-BLU-06 06
#
# Hardware alvo (o disponibilizado pelo prof. Lucas, em Joinville):
#   - LILYGO TTGO T-Beam V1.1  (ESP32 + SX1276 + GPS)
#   - Heltec LoRa 32 V3        (ESP32 + SX1262 + OLED)
#   - LILYGO TTGO LoRa T3S3 1.2
#   Antenas 5 dBi SMA 915 MHz / 8 dBi para ponto elevado; LiPo ou 18650 3,7 V.
#
# REGIAO: a tabela oficial "LoRa Region by Country" do Meshtastic lista o Brasil
# como "ANZ | BR_902". BR_902 opera em 902-907,5 MHz. Como a atividade EXIGE
# 915 MHz, a regiao correta e ANZ (915-928 MHz).
#
# OBS: o Meshtastic reinicia a cada comando; por isso os --set sao encadeados.
# NENHUMA credencial neste arquivo. A PSK do canal e gerada localmente e NAO
# entra no repositorio (ver .gitignore).
# =============================================================================
set -euo pipefail

PORT="${1:-/dev/ttyUSB0}"
LONGNAME="${2:-ATG-BLU-06}"
SHORTNAME="${3:-06}"

echo ">> Radio: regiao ANZ (915 MHz), preset LongFast, 3 saltos, 22 dBm"
meshtastic --port "$PORT" \
  --set lora.region ANZ \
  --set lora.modem_preset LONG_FAST \
  --set lora.hop_limit 3 \
  --set lora.tx_power 22 \
  --set lora.tx_enabled true

echo ">> Identidade do no"
meshtastic --port "$PORT" --set-owner "$LONGNAME" --set-owner-short "$SHORTNAME"

echo ">> Canal primario ATG-Blumenau (uplink + downlink habilitados)"
# A PSK deve ser gerada por voce e NAO versionada:
#   PSK=$(openssl rand -base64 32)
meshtastic --port "$PORT" \
  --ch-index 0 --ch-set name ATG-Blumenau \
  --ch-set uplink_enabled true \
  --ch-set downlink_enabled true

echo ">> Canal 'mqtt' (obrigatorio para o DOWNLINK JSON chegar na malha)"
meshtastic --port "$PORT" --ch-add mqtt
meshtastic --port "$PORT" --ch-index 1 --ch-set downlink_enabled true

echo ">> Modulo MQTT com JSON habilitado (nao suportado em nRF52; ok no ESP32)"
meshtastic --port "$PORT" \
  --set mqtt.enabled true \
  --set mqtt.json_enabled true \
  --set mqtt.root msh/BR \
  --set mqtt.address "$MQTT_HOST" \
  --set mqtt.proxy_to_client_enabled true

echo ">> Telemetria de dispositivo e ambiente"
meshtastic --port "$PORT" \
  --set telemetry.device_update_interval 900 \
  --set telemetry.environment_measurement_enabled true \
  --set telemetry.environment_update_interval 900

echo ">> Papel do no (CLIENT | ROUTER para os repetidores)"
# meshtastic --port "$PORT" --set device.role ROUTER

echo ">> Conferencia"
meshtastic --port "$PORT" --info
