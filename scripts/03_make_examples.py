#!/usr/bin/env python3
"""Gera examples/ : payloads validos, invalidos, envelopes MQTT e wire formats."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atg_mesh import codec, meshtastic_json as mj  # noqa: E402
from atg_mesh.schema import ATG_ENV_SCHEMA, EXAMPLE  # noqa: E402
from atg_mesh.validator import parse_and_validate  # noqa: E402

EX = ROOT / "examples"
EX.mkdir(exist_ok=True)


def w(name, obj):
    (EX / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    print(f"  examples/{name}")


rain = json.loads(json.dumps(EXAMPLE))
rain.update({
    "device_id": "!a76c0001", "node_num": int("a76c0001", 16),
    "node_name": "ATG-BLU-01 Vila Itoupava", "latitude": -26.7550,
    "longitude": -49.0906, "altitude": 30.0, "site": "Vila Itoupava",
    "sensor_type": "rain_gauge", "sensor_value": 21.4, "unit": "mm",
    "rate_of_change": None, "accum_24h_mm": 118.2,
    "risk_level": "attention", "alertablu_stage": None,
    "alert_message": ("[ATG-BLU] ATENCAO: Chuva 21.4mm/h em Vila Itoupava. "
                      "24h=118mm. Acompanhe o AlertaBlu. Emerg 199/193. 12/07 14:00"),
    "source": "openmeteo_era5",
    "radio": {"rssi_dbm": -122.7, "snr_db": -8.7, "hops": 2,
              "preset": "LONG_FAST", "region": "ANZ"},
})

INVALID = {
    "campo_minimo_ausente": {k: v for k, v in EXAMPLE.items() if k != "sensor_value"},
    "enum_de_risco_invalido": {**EXAMPLE, "risk_level": "panic"},
    "timestamp_nao_iso8601": {**EXAMPLE, "timestamp": "12/07/2026 14:00"},
    "coordenada_fora_do_vale_do_itajai": {**EXAMPLE, "latitude": 48.8566,
                                          "longitude": 2.3522},
    "nivel_implausivel": {**EXAMPLE, "sensor_value": 999.0},
    "unidade_incoerente_com_o_sensor": {**EXAMPLE, "unit": "mm"},
    "device_id_sem_prefixo": {**EXAMPLE, "device_id": "a76c0006"},
}


def main():
    print("Gerando exemplos:")
    w("atg-env-1.0.schema.json", ATG_ENV_SCHEMA)
    w("payload_river_level.json", EXAMPLE)
    w("payload_rain_gauge.json", rain)

    w("wire_formats.json", {
        "_doc": ("As tres representacoes do MESMO payload. Somente as compactas "
                 "cabem no Data.payload do Meshtastic (233 B)."),
        "atg-env/1.0 (canonico, bytes)": len(json.dumps(EXAMPLE).encode()),
        "ATG-C1-JSON": codec.to_c1_json(EXAMPLE),
        "ATG-C1-JSON (bytes)": len(codec.to_c1_json(EXAMPLE).encode()),
        "ATG-C1-BIN (hex)": codec.to_c1_bin(EXAMPLE).hex(),
        "ATG-C1-BIN (bytes)": len(codec.to_c1_bin(EXAMPLE)),
        "ATG-C1-B64 (TEXT_MESSAGE_APP)": codec.to_c1_b64(EXAMPLE),
        "ATG-C1-B64 (bytes)": len(codec.to_c1_b64(EXAMPLE).encode()),
        "decodificado_de_volta": codec.from_c1_bin(codec.to_c1_bin(EXAMPLE)),
        "tamanhos": {k: v for k, v in codec.size_report(EXAMPLE).items()
                     if not k.startswith("_")},
    })

    w("meshtastic_mqtt.json", {
        "_doc": "Envelopes JSON reais do ecossistema Meshtastic (mqtt.json_enabled=true).",
        "uplink": {
            "topic": mj.uplink_topic(),
            "message": mj.uplink_telemetry(EXAMPLE, rssi=-98.4, snr=8.1, hops=1),
        },
        "uplink_texto_do_alerta": {
            "topic": mj.uplink_topic(),
            "message": mj.uplink_text(EXAMPLE, rssi=-98.4, snr=8.1, hops=1),
        },
        "downlink_para_o_celular": {
            "topic": mj.downlink_topic(),
            "_requisito": ("exige um canal chamado exatamente 'mqtt' com "
                           "downlink habilitado; campos 'from' e 'payload' "
                           "sao obrigatorios (firmware >= 2.2.20)"),
            "message": mj.downlink_sendtext(EXAMPLE["alert_message"]),
        },
        "topico_protobuf_equivalente": mj.protobuf_topic(),
        "contexto_de_radio": mj.RADIO_CONTEXT,
    })

    bad = {}
    for k, p in INVALID.items():
        res = parse_and_validate(json.dumps(p))
        bad[k] = {"payload": p, "aceito": res.ok, "erros": res.errors}
    bad["_json_malformado"] = {
        "payload_raw": '{"schema": "atg-env/1.0", ',
        "aceito": False,
        "erros": parse_and_validate('{"schema": "atg-env/1.0", ').errors,
    }
    w("payloads_invalidos_e_erros.json", bad)


if __name__ == "__main__":
    main()
