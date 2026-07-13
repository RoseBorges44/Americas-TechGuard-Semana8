"""
Pipeline fim-a-fim do Periodo 8.

sensor -> ATG-ENV 1.0 (JSON) -> validacao -> regra de risco -> ATG-C1 (compacto)
-> malha LoRa/Meshtastic (managed flooding) -> gateway -> MQTT/JSON -> alerta
para celular (downlink sendtext).
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import alert, codec, ingest, lora, meshtastic_json as mj, risk
from .config import (ACTIVE_PRESET, DATA_PAYLOAD_LEN, GATEWAY, MESHTASTIC_REGION,
                     NODES, UNITS)
from .mesh_sim import MeshNetwork
from .validator import parse_and_validate

LOG = logging.getLogger("atg")

# Intervalo adaptativo de reporte (s). Zakaria et al. usam periodo fixo de 60 s;
# aqui o periodo e funcao do risco, para economizar bateria e airtime em calmaria
# e ganhar resolucao temporal na crise.
REPORT_INTERVAL_S = {"safe": 3600, "attention": 900, "alert": 300, "critical": 60}


def _iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payloads(data: dict, thresholds: risk.RainThresholds) -> list[dict]:
    rain, river = data["rain"], data["river"]
    src_rain, src_river = data["source_rain"], data["source_river"]

    river = river.sort_values("time").reset_index(drop=True)
    river["rate"] = river["stage_m"].diff()          # passo horario -> m/h
    rain = rain.sort_values(["station", "time"]).copy()
    rain["accum24"] = (rain.groupby("station")["rain_mm"]
                       .rolling(24, min_periods=1).sum()
                       .reset_index(level=0, drop=True))

    basin_rain24 = rain.groupby("time")["accum24"].mean()
    payloads: list[dict] = []
    rng = random.Random(11)

    for node in NODES:
        if node.sensor_type == "rain_gauge":
            sub = rain[rain["station"] == node.name]
            for _, r in sub.iterrows():
                a = risk.classify_rain(float(r["rain_mm"]), float(r["accum24"]),
                                       thresholds)
                p = _payload(node, r["time"], float(r["rain_mm"]), "mm", a,
                             rate=None, accum=float(r["accum24"]), source=src_rain,
                             rng=rng)
                payloads.append(p)

        elif node.sensor_type == "river_level":
            for _, r in river.iterrows():
                rate = None if pd.isna(r["rate"]) else float(r["rate"])
                acc = float(basin_rain24.get(r["time"], np.nan))
                acc = None if np.isnan(acc) else acc
                a_river = risk.classify_river(float(r["stage_m"]), rate)
                if acc is not None:
                    a_rain = risk.classify_rain(0.0, acc, thresholds)
                    a = risk.combine(a_river, a_rain)
                else:
                    a = a_river
                p = _payload(node, r["time"], float(r["stage_m"]), "m", a,
                             rate=rate, accum=acc, source=src_river, rng=rng)
                payloads.append(p)

    payloads.sort(key=lambda p: (p["timestamp"], p["device_id"]))
    return payloads


def _payload(node, ts, value, unit, assessment, *, rate, accum, source, rng) -> dict:
    site = node.site or node.name
    local = pd.Timestamp(ts).tz_convert("America/Sao_Paulo").strftime("%d/%m %H:%M")
    msg = alert.build_message(
        sensor_type=node.sensor_type, value=value, unit=unit,
        risk_level=assessment.risk_level, site=site, rate=rate,
        accum_24h_mm=accum, timestamp_local=local,
    )
    return {
        "schema": "atg-env/1.0",
        "device_id": node.node_id,
        "node_num": node.node_num,
        "node_name": node.name,
        "timestamp": _iso(ts),
        "latitude": node.lat,
        "longitude": node.lon,
        "altitude": float(node.alt_m),
        "site": site,
        "sensor_type": node.sensor_type,
        "sensor_value": round(value, 3),
        "unit": unit,
        "rate_of_change": None if rate is None else round(rate, 3),
        "accum_24h_mm": None if accum is None else round(accum, 1),
        "risk_level": assessment.risk_level,
        "alertablu_stage": assessment.alertablu_stage,
        "alert_message": msg,
        "source": source,
        "quality": "ok",
        "battery_pct": max(35, 100 - rng.randint(0, 25)),
        "fw": "atg-node/1.0",
        "radio": {"rssi_dbm": None, "snr_db": None, "hops": None,
                  "preset": ACTIVE_PRESET, "region": MESHTASTIC_REGION},
    }


def run(*, offline: bool, start: str, end: str, outdir: Path, cache: Path,
        seed: int = 42) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "figures").mkdir(exist_ok=True)

    LOG.info("1/6 ingestao de dados (offline=%s, janela %s..%s)", offline, start, end)
    data = ingest.load_event(offline=offline, start=start, end=end, cache=cache)
    LOG.info("    fonte chuva=%s | fonte rio=%s | %s",
             data["source_rain"], data["source_river"], data["note"])

    clim = data["rain_climatology"].sort_values(["station", "time"])
    clim_acc = (clim.groupby("station")["rain_mm"]
                .rolling(24, min_periods=24).sum().dropna().tolist())
    th = risk.RainThresholds.from_series(
        clim["rain_mm"].tolist(), clim_acc, label=data["clim_label"])
    LOG.info("2/6 limiares de chuva: %s", th.as_dict())

    payloads = build_payloads(data, th)
    LOG.info("3/6 %d payloads ATG-ENV gerados", len(payloads))

    # ---- validacao (inclui casos negativos propositais) -------------------
    n_ok, n_bad, errs = 0, 0, []
    for p in payloads:
        res = parse_and_validate(json.dumps(p))
        if res.ok:
            n_ok += 1
        else:
            n_bad += 1
            errs.extend(res.errors[:2])
    LOG.info("4/6 validacao: %d validos, %d invalidos", n_ok, n_bad)
    if errs:
        LOG.warning("    exemplos de erro: %s", errs[:3])

    # ---- codificacao + malha ---------------------------------------------
    sizes = codec.size_report(payloads[0])
    net = MeshNetwork(seed=seed)
    star = MeshNetwork(seed=seed, hop_limit=1)   # baseline "estrela" tipo LoRaWAN
    mesh_rows, star_rows, mqtt_lines, alerts = [], [], [], []
    last_risk: dict[str, str] = {}
    pid = 1

    for p in payloads:
        wire = codec.to_c1_bin(p)
        assert len(wire) <= DATA_PAYLOAD_LEN
        res = net.send(p["device_id"], len(wire), pid)
        star_rows.append({"origin_name": p["node_name"],
                          "delivered": star.send(p["device_id"], len(wire),
                                                 pid).delivered})
        pid += 1

        mesh_rows.append({
            "packet_id": res.packet_id, "timestamp": p["timestamp"],
            "origin": res.origin, "origin_name": p["node_name"],
            "risk": p["risk_level"], "payload_bytes": len(wire),
            "delivered": res.delivered, "hops": res.hops,
            "latency_s": res.latency_s, "rssi_dbm": res.rssi_dbm,
            "snr_db": res.snr_db, "n_tx": len(res.transmissions),
            "airtime_s": round(res.total_airtime_s, 3),
            "path": " -> ".join(res.path),
        })

        if not res.delivered:
            continue

        # gateway: reidrata o payload compacto e republica em MQTT/JSON
        rehydrated = codec.from_c1_bin(wire)
        rehydrated["radio"] = {"rssi_dbm": res.rssi_dbm, "snr_db": res.snr_db,
                               "hops": res.hops, "preset": ACTIVE_PRESET,
                               "region": MESHTASTIC_REGION}
        up = mj.uplink_telemetry(p, rssi=res.rssi_dbm, snr=res.snr_db,
                                 hops=res.hops)
        mqtt_lines.append(mj.as_mqtt_line(mj.uplink_topic(), up))

        # alerta so quando o estado MUDA para pior, ou re-emissao horaria em
        # 'critical' (evita spam e economiza airtime/bateria)
        prev = last_risk.get(p["device_id"], "safe")
        escalated = (risk.RISK_ORDER.index(p["risk_level"])
                     > risk.RISK_ORDER.index(prev))
        if escalated or (p["risk_level"] == "critical" and prev == "critical"
                         and pid % 6 == 0):
            dl = mj.downlink_sendtext(p["alert_message"])
            mqtt_lines.append(mj.as_mqtt_line(mj.downlink_topic(), dl))
            up_txt = mj.uplink_text(p, rssi=res.rssi_dbm, snr=res.snr_db,
                                    hops=res.hops)
            mqtt_lines.append(mj.as_mqtt_line(mj.uplink_topic(), up_txt))
            alerts.append({"timestamp": p["timestamp"], "device_id": p["device_id"],
                           "node_name": p["node_name"], "from": prev,
                           "to": p["risk_level"],
                           "alertablu_stage": p["alertablu_stage"],
                           "sensor_value": p["sensor_value"], "unit": p["unit"],
                           "rate_of_change": p["rate_of_change"],
                           "alert_message": p["alert_message"],
                           "chars": len(p["alert_message"]),
                           "downlink_topic": mj.downlink_topic()})
        last_risk[p["device_id"]] = p["risk_level"]

    mesh = pd.DataFrame(mesh_rows)
    starf = pd.DataFrame(star_rows)
    LOG.info("5/6 malha: PDR=%.1f%% | saltos medios=%.2f | latencia mediana=%.2fs",
             100 * mesh["delivered"].mean(),
             mesh.loc[mesh.delivered, "hops"].mean(),
             mesh.loc[mesh.delivered, "latency_s"].median())
    LOG.info("    baseline estrela (hop_limit=1, tipo LoRaWAN): PDR=%.1f%%",
             100 * starf["delivered"].mean())
    LOG.info("    alertas emitidos para celular: %d", len(alerts))

    # ---- duty cycle / autonomia -------------------------------------------
    toa = lora.time_on_air_s(len(codec.to_c1_bin(payloads[0])))
    duty = {k: round(100 * toa / v, 4) for k, v in REPORT_INTERVAL_S.items()}

    metrics = {
        "run": {"offline": offline, "window": [start, end], "seed": seed,
                "preset": ACTIVE_PRESET, "region": MESHTASTIC_REGION},
        "data": {"source_rain": data["source_rain"],
                 "source_river": data["source_river"],
                 "note": data["note"],
                 "rain_thresholds": th.as_dict(),
                 "rating_curve": data.get("rating")},
        "payloads": {"total": len(payloads), "valid": n_ok, "invalid": n_bad,
                     "by_risk": pd.Series([p["risk_level"] for p in payloads])
                     .value_counts().to_dict()},
        "payload_sizes_bytes": {k: v for k, v in sizes.items()
                                if not k.startswith("_")},
        "lora": {
            "time_on_air_s": round(toa, 4),
            "bitrate_bps": round(lora.bitrate_bps(), 1),
            "duty_cycle_pct_by_risk": duty,
            "toa_by_preset_s": {k: round(lora.time_on_air_s(
                len(codec.to_c1_bin(payloads[0])), k), 4) for k in
                ["LONG_FAST", "LONG_SLOW", "MEDIUM_FAST", "SHORT_FAST"]},
            "toa_if_full_json_s": "N/A - JSON canonico (%d B) excede DATA_PAYLOAD_LEN (%d B)"
                                  % (sizes["_full_len"], DATA_PAYLOAD_LEN),
        },
        "mesh": {
            "packets": int(len(mesh)),
            "pdr_pct": round(100 * float(mesh["delivered"].mean()), 2),
            "mean_hops": round(float(mesh.loc[mesh.delivered, "hops"].mean()), 2),
            "median_latency_s": round(float(mesh.loc[mesh.delivered, "latency_s"].median()), 3),
            "p95_latency_s": round(float(mesh.loc[mesh.delivered, "latency_s"].quantile(.95)), 3),
            "mean_rssi_dbm": round(float(mesh.loc[mesh.delivered, "rssi_dbm"].mean()), 1),
            "mean_snr_db": round(float(mesh.loc[mesh.delivered, "snr_db"].mean()), 1),
            "pdr_by_node": (mesh.groupby("origin_name")["delivered"]
                            .mean().mul(100).round(1).to_dict()),
            "mean_tx_per_packet": round(float(mesh["n_tx"].mean()), 2),
        },
        "star_baseline_hop_limit_1": {
            "_comment": ("Mesma fisica, sem retransmissao: equivale a uma "
                         "topologia estrela (no -> gateway), como no LoRaWAN "
                         "de Zakaria et al. Mede o ganho real do mesh."),
            "pdr_pct": round(100 * float(starf["delivered"].mean()), 2),
            "pdr_by_node": (starf.groupby("origin_name")["delivered"]
                            .mean().mul(100).round(1).to_dict()),
        },
        "alerts": {"total": len(alerts),
                   "max_chars": max((a["chars"] for a in alerts), default=0)},
    }

    # ---- gravacao ----------------------------------------------------------
    with open(outdir / "payloads.jsonl", "w") as f:
        for p in payloads:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(outdir / "alerts.jsonl", "w") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    with open(outdir / "mqtt_log.txt", "w") as f:
        f.write("\n".join(mqtt_lines))
    mesh.to_csv(outdir / "mesh_log.csv", index=False)
    starf.to_csv(outdir / "star_baseline_log.csv", index=False)
    pd.DataFrame(net.topology()).to_csv(outdir / "topology.csv", index=False)
    with open(outdir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=str)

    LOG.info("6/6 saidas gravadas em %s", outdir)
    return {"metrics": metrics, "payloads": payloads, "mesh": mesh,
            "alerts": alerts, "data": data, "net": net}
