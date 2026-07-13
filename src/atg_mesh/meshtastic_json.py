"""
Interoperabilidade com o ecossistema Meshtastic via MQTT/JSON.

Referencia: documentacao oficial "MQTT Module Configuration" e "Integrations >
MQTT". Pontos que a implementacao respeita literalmente:

UPLINK (no -> broker), quando mqtt.json_enabled=true:
  topico: msh/<REGION>/2/json/<CHANNELNAME>/<USERID>
  envelope: {"channel":0,"from":<decimal>,"id":<int>,"payload":{...},
             "sender":"!<hex do gateway>","timestamp":<epoch>,
             "to":4294967295,"type":"telemetry|text|position|nodeinfo",
             "rssi":..,"snr":..,"hops_away":..}
  Somente alguns portnums sao serializados em JSON: TEXT_MESSAGE_APP,
  TELEMETRY_APP, POSITION_APP, NODEINFO_APP, DETECTION_SENSOR_APP, etc.

DOWNLINK (broker -> mesh -> celular):
  topico: msh/<REGION>/2/json/mqtt/
  exige um canal chamado exatamente "mqtt" com downlink habilitado.
  campos obrigatorios: "from" e "payload". "type":"sendtext" injeta um
  TEXT_MESSAGE_APP na malha, que aparece no app Meshtastic do celular.

Limitacao documentada: JSON via MQTT nao e suportado na plataforma nRF52; nas
placas ESP32 da atividade (T-Beam V1.1, Heltec V3, T3S3) e suportado.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from .config import (ACTIVE_PRESET, GATEWAY, MESH_CHANNEL,
                     MESH_DOWNLINK_CHANNEL, MESHTASTIC_REGION, MQTT_ROOT)

BROADCAST = 4294967295


def hex_to_num(node_id: str) -> int:
    return int(node_id.lstrip("!"), 16)


def num_to_hex(node_num: int) -> str:
    return f"!{node_num & 0xFFFFFFFF:08x}"


def uplink_topic(channel: str = MESH_CHANNEL,
                 gateway_id: str = GATEWAY.node_id) -> str:
    return f"{MQTT_ROOT}/2/json/{channel}/{gateway_id}"


def protobuf_topic(channel: str = MESH_CHANNEL,
                   gateway_id: str = GATEWAY.node_id) -> str:
    """Topico do ServiceEnvelope protobuf cru (nao usado aqui, documentado)."""
    return f"{MQTT_ROOT}/2/e/{channel}/{gateway_id}"


def downlink_topic() -> str:
    return f"{MQTT_ROOT}/2/json/{MESH_DOWNLINK_CHANNEL}/"


def _envelope(from_num: int, ptype: str, payload, *, rssi=None, snr=None,
              hops=None, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    env = {
        "channel": 0,
        "from": from_num,
        "id": rng.getrandbits(31),
        "payload": payload,
        "sender": GATEWAY.node_id,
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "to": BROADCAST,
        "type": ptype,
    }
    if rssi is not None:
        env["rssi"] = rssi
    if snr is not None:
        env["snr"] = snr
    if hops is not None:
        env["hops_away"] = hops
    return env


def uplink_telemetry(p: dict, *, rssi=None, snr=None, hops=None,
                     rng=None) -> dict:
    """
    Telemetria ambiental no envelope Meshtastic.

    O modulo Telemetry oficial cobre device/environment/air-quality metrics.
    Nivel de rio e chuva acumulada nao existem no protobuf EnvironmentMetrics;
    por isso o ATG-ENV viaja no campo "payload" do envelope JSON, e a
    equivalencia com TELEMETRY_APP e explicitada no README (ETAPA 4).
    """
    inner = {
        "atg": "atg-env/1.0",
        "sensor_type": p["sensor_type"],
        "sensor_value": p["sensor_value"],
        "unit": p["unit"],
        "risk_level": p["risk_level"],
        "rate_of_change": p.get("rate_of_change"),
        "battery_level": p.get("battery_pct", 100),
        "latitude_i": int(round(p["latitude"] * 1e7)),
        "longitude_i": int(round(p["longitude"] * 1e7)),
    }
    return _envelope(p["node_num"], "telemetry", inner,
                     rssi=rssi, snr=snr, hops=hops, rng=rng)


def uplink_text(p: dict, *, rssi=None, snr=None, hops=None, rng=None) -> dict:
    return _envelope(p["node_num"], "text", {"text": p["alert_message"]},
                     rssi=rssi, snr=snr, hops=hops, rng=rng)


def downlink_sendtext(text: str, *, from_num: int | None = None,
                      to_num: int = BROADCAST, channel: int = 0) -> dict:
    """
    Mensagem que o backend do Americas TechGuard publica no broker para que a
    malha a difunda ate os celulares pareados por Bluetooth ao app Meshtastic.
    Campos obrigatorios pelo firmware >= 2.2.20: "from" e "payload".
    """
    return {
        "from": from_num or GATEWAY.node_num,
        "to": to_num,
        "channel": channel,
        "type": "sendtext",
        "payload": text,
    }


def as_mqtt_line(topic: str, obj: dict) -> str:
    return f"{topic} {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}"


RADIO_CONTEXT = {
    "region": MESHTASTIC_REGION,
    "modem_preset": ACTIVE_PRESET,
    "primary_channel": MESH_CHANNEL,
    "downlink_channel": MESH_DOWNLINK_CHANNEL,
    "mqtt_root": MQTT_ROOT,
}
