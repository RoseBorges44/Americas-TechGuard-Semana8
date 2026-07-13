"""
ATG-C1: codec de fio (wire format) para caber no payload do Meshtastic.

Problema real: o payload canonico ATG-ENV 1.0 (legivel, autoexplicativo) tem
~600-700 bytes. O campo Data.payload do Meshtastic aceita 233 bytes
(DATA_PAYLOAD_LEN), dentro de um pacote LoRa de no maximo 256 bytes. Ou seja,
o JSON canonico NAO passa pelo radio. Esta e a restricao central da atividade.

Solucao em tres niveis, todos implementados e medidos:

  L0  atg-env/1.0     JSON canonico          -> usado em MQTT/API/banco
  L1  ATG-C1-JSON     JSON minificado, chaves de 1-2 letras, inteiros escalados
  L2  ATG-C1-BIN      struct binario de 23 bytes (little-endian)
                      -> transportado como bytes crus em PRIVATE_APP, ou em
                         Base64 (32 chars) dentro de um TEXT_MESSAGE_APP.

O gateway reidrata L1/L2 de volta para o ATG-ENV 1.0 completo (o alert_message
e reconstruido localmente a partir dos campos, nao viaja pelo radio).

Layout ATG-C1-BIN (23 bytes):
  offset  tam  campo
  0       1    ver_type : nibble alto = versao (1), nibble baixo = codigo sensor
  1       4    node_num : uint32  (User ID Meshtastic em decimal)
  5       4    ts       : uint32  (unix epoch, segundos)
  9       4    lat_i    : int32   (graus * 1e6  -> ~0,11 m de resolucao)
  13      4    lon_i    : int32   (graus * 1e6)
  17      2    val      : int16   (escala por tipo de sensor, ver SCALE)
  19      2    rate     : int16   (taxa * 1000, por hora)
  21      1    risk     : uint8   (0..3)
  22      1    batt     : uint8   (0..100 %)
"""
from __future__ import annotations

import base64
import json
import struct
from datetime import datetime, timezone

from .config import (RISK_CODE, RISK_CODE_INV, SENSOR_CODE, SENSOR_CODE_INV,
                     UNITS)

VERSION = 1
STRUCT_FMT = "<BIIiihhBB"
STRUCT_SIZE = struct.calcsize(STRUCT_FMT)  # 23

# Escala por tipo de sensor para caber em int16 (-32768..32767)
SCALE = {
    "river_level": 100.0,       # metros -> centimetros  (max 327,67 m)
    "rain_gauge": 10.0,         # mm     -> decimos de mm (max 3276,7 mm)
    "river_discharge": 1.0,     # m3/s inteiros          (max 32767 m3/s)
    "repeater": 1.0,
    "gateway": 1.0,
}

# Mapa de chaves curtas do ATG-C1-JSON
SHORT = {
    "device_id": "d", "timestamp": "t", "latitude": "y", "longitude": "x",
    "sensor_type": "s", "sensor_value": "v", "unit": "u", "risk_level": "r",
    "rate_of_change": "c", "battery_pct": "b", "node_num": "n",
}
SHORT_INV = {v: k for k, v in SHORT.items()}


# ---------------------------------------------------------------- L1: JSON compacto
def to_c1_json(p: dict) -> str:
    ts = int(datetime.fromisoformat(
        p["timestamp"].replace("Z", "+00:00")).timestamp())
    obj = {
        "v": 1,
        SHORT["node_num"]: p["node_num"],
        SHORT["timestamp"]: ts,
        SHORT["latitude"]: round(p["latitude"], 5),
        SHORT["longitude"]: round(p["longitude"], 5),
        SHORT["sensor_type"]: SENSOR_CODE[p["sensor_type"]],
        "w": round(p["sensor_value"], 2),
        SHORT["risk_level"]: RISK_CODE[p["risk_level"]],
        SHORT["rate_of_change"]: (None if p.get("rate_of_change") is None
                                  else round(p["rate_of_change"], 3)),
        SHORT["battery_pct"]: p.get("battery_pct", 100),
    }
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)


def from_c1_json(s: str) -> dict:
    o = json.loads(s)
    stype = SENSOR_CODE_INV[o["s"]]
    return {
        "node_num": o["n"],
        "timestamp": datetime.fromtimestamp(o["t"], tz=timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": o["y"], "longitude": o["x"],
        "sensor_type": stype, "sensor_value": o["w"],
        "unit": UNITS.get(stype, "none"),
        "risk_level": RISK_CODE_INV[o["r"]],
        "rate_of_change": o.get("c"),
        "battery_pct": o.get("b", 100),
    }


# ---------------------------------------------------------------- L2: binario
def to_c1_bin(p: dict) -> bytes:
    stype = p["sensor_type"]
    ver_type = (VERSION << 4) | SENSOR_CODE[stype]
    ts = int(datetime.fromisoformat(
        p["timestamp"].replace("Z", "+00:00")).timestamp())
    val = int(round(p["sensor_value"] * SCALE[stype]))
    rate = p.get("rate_of_change")
    rate_i = 0 if rate is None else int(round(rate * 1000))
    rate_i = max(-32768, min(32767, rate_i))
    val = max(-32768, min(32767, val))
    return struct.pack(
        STRUCT_FMT,
        ver_type,
        p["node_num"] & 0xFFFFFFFF,
        ts,
        int(round(p["latitude"] * 1e6)),
        int(round(p["longitude"] * 1e6)),
        val,
        rate_i,
        RISK_CODE[p["risk_level"]],
        int(p.get("battery_pct", 100)),
    )


def from_c1_bin(buf: bytes) -> dict:
    if len(buf) != STRUCT_SIZE:
        raise ValueError(f"ATG-C1-BIN espera {STRUCT_SIZE} bytes, recebeu {len(buf)}")
    (ver_type, node_num, ts, lat_i, lon_i, val, rate_i, risk,
     batt) = struct.unpack(STRUCT_FMT, buf)
    ver, scode = ver_type >> 4, ver_type & 0x0F
    if ver != VERSION:
        raise ValueError(f"versao ATG-C1 desconhecida: {ver}")
    stype = SENSOR_CODE_INV[scode]
    return {
        "node_num": node_num,
        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": lat_i / 1e6, "longitude": lon_i / 1e6,
        "sensor_type": stype, "sensor_value": val / SCALE[stype],
        "unit": UNITS.get(stype, "none"),
        "risk_level": RISK_CODE_INV[risk],
        "rate_of_change": rate_i / 1000.0,
        "battery_pct": batt,
    }


def to_c1_b64(p: dict) -> str:
    """Binario em Base64 - cabe folgado em um TEXT_MESSAGE_APP."""
    return base64.b64encode(to_c1_bin(p)).decode("ascii")


def from_c1_b64(s: str) -> dict:
    return from_c1_bin(base64.b64decode(s))


# ---------------------------------------------------------------- medicao
def size_report(p: dict) -> dict:
    """Tamanhos em bytes de cada representacao (evidencia da ETAPA 5)."""
    full = json.dumps(p, ensure_ascii=False)
    return {
        "atg-env/1.0 (JSON canonico, indentado)": len(json.dumps(p, indent=2).encode()),
        "atg-env/1.0 (JSON minificado)": len(json.dumps(
            p, separators=(",", ":"), ensure_ascii=False).encode()),
        "ATG-C1-JSON": len(to_c1_json(p).encode()),
        "ATG-C1-BIN (bytes crus, PRIVATE_APP)": len(to_c1_bin(p)),
        "ATG-C1-B64 (dentro de TEXT_MESSAGE_APP)": len(to_c1_b64(p).encode()),
        "alert_message (texto humano)": len(p.get("alert_message", "").encode()),
        "_full_len": len(full.encode()),
    }
