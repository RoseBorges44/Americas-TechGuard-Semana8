"""
ATG-ENV v1.0 - Schema JSON canonico de leitura ambiental / alerta.

E o "contrato" da camada intermediaria entre sensores, rede LoRa/Meshtastic,
APIs, banco de dados e sistemas de alerta do Americas TechGuard.

Campos minimos exigidos pelo enunciado do Periodo 8 (ETAPA 2):
device_id, timestamp, latitude, longitude, sensor_type, sensor_value, unit,
risk_level, alert_message, source.

Campos adicionais justificados:
- node_name / node_num : identidade Meshtastic (User ID hex e equivalente decimal)
- altitude             : usado no modelo de propagacao e no contexto hidrologico
- rate_of_change       : herdado do Algoritmo 1 de Zakaria et al. (2023), que
                         classifica risco tambem pela taxa de variacao do nivel
- alertablu_stage      : estagio oficial da Defesa Civil de Blumenau (5 niveis)
- quality              : flag de qualidade do dado (ok | stale | out_of_range)
- radio                : metricas de enlace (rssi, snr, hops) preenchidas pelo
                         gateway ao receber, espelhando RSSI/SNR/PDR dos artigos
- schema               : versionamento explicito do payload
"""
from __future__ import annotations

SCHEMA_ID = "https://github.com/RoseBorges44/Americas-TechGuard-Semana8/atg-env-1.0.json"

ATG_ENV_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "title": "ATG-ENV 1.0 - Americas TechGuard environmental payload",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema", "device_id", "timestamp", "latitude", "longitude",
        "sensor_type", "sensor_value", "unit", "risk_level",
        "alert_message", "source",
    ],
    "properties": {
        "schema": {"const": "atg-env/1.0"},
        "device_id": {
            "type": "string",
            "pattern": r"^![0-9a-f]{8}$",
            "description": "Meshtastic User ID em hexadecimal.",
        },
        "node_num": {
            "type": "integer", "minimum": 0, "maximum": 4294967295,
            "description": "Equivalente decimal do device_id (campo 'from' no JSON MQTT).",
        },
        "node_name": {"type": "string", "maxLength": 64},
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 com timezone (UTC).",
        },
        "latitude": {"type": "number", "minimum": -90, "maximum": 90},
        "longitude": {"type": "number", "minimum": -180, "maximum": 180},
        "altitude": {"type": "number", "minimum": -500, "maximum": 9000},
        "site": {"type": "string", "maxLength": 64},
        "sensor_type": {
            "type": "string",
            "enum": ["river_level", "rain_gauge", "river_discharge",
                     "repeater", "gateway"],
        },
        "sensor_value": {"type": "number"},
        "unit": {"type": "string", "enum": ["m", "mm", "m3/s", "none"]},
        "rate_of_change": {
            "type": ["number", "null"],
            "description": "Variacao por hora na unidade do sensor (m/h ou mm/h).",
        },
        "accum_24h_mm": {"type": ["number", "null"], "minimum": 0},
        "risk_level": {
            "type": "string",
            "enum": ["safe", "attention", "alert", "critical"],
        },
        "alertablu_stage": {
            "type": ["string", "null"],
            "enum": ["normalidade", "observacao", "atencao", "alerta",
                     "alerta_maximo", None],
        },
        "alert_message": {"type": "string", "maxLength": 200},
        "source": {
            "type": "string",
            "enum": ["hardware", "simulation", "synthetic", "csv", "mocked_api",
                     "manual", "openmeteo_era5", "glofas_openmeteo"],
        },
        "quality": {"type": "string", "enum": ["ok", "stale", "out_of_range"]},
        "battery_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "fw": {"type": "string", "maxLength": 24},
        "radio": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rssi_dbm": {"type": ["number", "null"]},
                "snr_db": {"type": ["number", "null"]},
                "hops": {"type": ["integer", "null"], "minimum": 0, "maximum": 7},
                "preset": {"type": "string"},
                "region": {"type": "string"},
            },
        },
    },
}

# Exemplo canonico (tambem gravado em examples/payload_full_example.json)
EXAMPLE = {
    "schema": "atg-env/1.0",
    "device_id": "!a76c0006",
    "node_num": 2809331718,
    "node_name": "ATG-BLU-06 Prainha (regua fluviometrica)",
    "timestamp": "2026-07-12T14:00:00Z",
    "latitude": -26.9187,
    "longitude": -49.0665,
    "altitude": 12.0,
    "site": "Prainha - Rio Itajai-Acu",
    "sensor_type": "river_level",
    "sensor_value": 6.42,
    "unit": "m",
    "rate_of_change": 0.28,
    "accum_24h_mm": 96.4,
    "risk_level": "alert",
    "alertablu_stage": "alerta",
    "alert_message": ("[ATG-BLU] ALERTA: Rio Itajai-Acu 6.42m (+0.28m/h) na Prainha. "
                      "Cota de alerta 6m. Evite margens. Emergencia 199/193."),
    "source": "glofas_openmeteo",
    "quality": "ok",
    "battery_pct": 87,
    "fw": "atg-node/1.0",
    "radio": {"rssi_dbm": -98.4, "snr_db": 8.1, "hops": 1,
              "preset": "LONG_FAST", "region": "ANZ"},
}
