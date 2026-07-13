#!/usr/bin/env python3
"""Gera as figuras de evidencia em outputs/figures/ a partir de outputs/."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atg_mesh.config import (ALERTABLU_STAGE_LADDER, DATA_PAYLOAD_LEN,  # noqa
                             NODES, NODE_BY_ID)

OUT = ROOT / "outputs"
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

C = {"safe": "#2e7d32", "attention": "#f9a825", "alert": "#ef6c00",
     "critical": "#c62828"}
BAND = ["#e8f5e9", "#fffde7", "#fff3e0", "#ffebee", "#ffcdd2"]


def load():
    pl = pd.read_json(OUT / "payloads.jsonl", lines=True)
    mesh = pd.read_csv(OUT / "mesh_log.csv")
    topo = pd.read_csv(OUT / "topology.csv")
    metrics = json.loads((OUT / "metrics.json").read_text())
    alerts = (pd.read_json(OUT / "alerts.jsonl", lines=True)
              if (OUT / "alerts.jsonl").stat().st_size else pd.DataFrame())
    pl["timestamp"] = pd.to_datetime(pl["timestamp"])
    return pl, mesh, topo, metrics, alerts


def fig1_hydro(pl, alerts):
    river = pl[pl.sensor_type == "river_level"].sort_values("timestamp")
    rain = (pl[pl.sensor_type == "rain_gauge"]
            .groupby("timestamp")["sensor_value"].mean())

    fig, ax1 = plt.subplots(figsize=(13, 5.6))
    for i, (lo, name, _) in enumerate(ALERTABLU_STAGE_LADDER):
        hi = (ALERTABLU_STAGE_LADDER[i + 1][0]
              if i + 1 < len(ALERTABLU_STAGE_LADDER) else 12)
        ax1.axhspan(lo, hi, color=BAND[i], zorder=0)
        ax1.text(river["timestamp"].iloc[2], lo + 0.12,
                 name.replace("_", " ").upper(), fontsize=7.5, color="#555")

    ax1.plot(river["timestamp"], river["sensor_value"], color="#0d47a1",
             lw=2.2, label="Nivel Rio Itajai-Acu (Prainha)", zorder=3)
    if len(alerts):
        a = alerts[alerts.device_id == "!a76c0006"]
        ax1.scatter(pd.to_datetime(a["timestamp"]), a["sensor_value"],
                    c=[C[r] for r in a["to"]], s=90, ec="k", lw=.6, zorder=5,
                    label="Alerta enviado ao celular")
    ax1.set_ylabel("Cota (m)")
    ax1.set_ylim(0, max(9.5, river["sensor_value"].max() + 1))
    ax1.set_xlabel("Tempo (UTC)")

    ax2 = ax1.twinx()
    ax2.bar(rain.index, rain.values, width=0.03, color="#1e88e5", alpha=.45,
            label="Chuva media da bacia (mm/h)")
    ax2.set_ylabel("Chuva (mm/h)")
    ax2.set_ylim(0, max(6, rain.max() * 3.2))
    ax2.invert_yaxis()

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)
    ax1.set_title("Fig. 1 - Evento: chuva observada, cota do rio e disparo dos "
                  "alertas (faixas = escada oficial AlertaBlu)")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_hidrograma_alertas.png", dpi=150)
    plt.close(fig)


def fig2_topology(topo):
    fig, ax = plt.subplots(figsize=(8.2, 8.6))
    for _, e in topo.iterrows():
        a, b = NODE_BY_ID[e["a"]], NODE_BY_ID[e["b"]]
        if not e["decodable"]:
            ax.plot([a.lon, b.lon], [a.lat, b.lat], color="#bbb", lw=.6,
                    ls=":", zorder=1)
            continue
        m = float(e["margin_db"])
        col = "#2e7d32" if m > 15 else "#f9a825" if m > 5 else "#c62828"
        ax.plot([a.lon, b.lon], [a.lat, b.lat], color=col,
                lw=1.0 + m / 12, alpha=.85, zorder=2)
        ax.text((a.lon + b.lon) / 2, (a.lat + b.lat) / 2,
                f"{e['distance_km']:.1f}km\n{e['snr_db']:.0f}dB",
                fontsize=6.2, ha="center", color="#333", zorder=3)

    for n in NODES:
        mk = {"GATEWAY": "s", "ROUTER": "^", "CLIENT": "o"}[n.role]
        cl = {"river_level": "#0d47a1", "rain_gauge": "#1e88e5",
              "repeater": "#6a1b9a", "gateway": "#000"}[n.sensor_type]
        ax.scatter(n.lon, n.lat, marker=mk, s=210, c=cl, ec="w", lw=1.4, zorder=5)
        ax.annotate(f"{n.name.split()[0]}\n{n.site}", (n.lon, n.lat),
                    textcoords="offset points", xytext=(10, 6), fontsize=7.6)

    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title("Fig. 2 - Topologia da malha Meshtastic em Blumenau\n"
                 "(915 MHz / ANZ / LongFast SF11 BW250; pontilhado = enlace "
                 "nao decodavel)")
    ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(FIG / "fig2_topologia_mesh.png", dpi=150)
    plt.close(fig)


def fig3_sizes(metrics):
    s = metrics["payload_sizes_bytes"]
    keys = list(s)[::-1]
    vals = [s[k] for k in keys]
    fig, ax = plt.subplots(figsize=(10.5, 4.4))
    cols = ["#c62828" if v > DATA_PAYLOAD_LEN else "#2e7d32" for v in vals]
    ax.barh(keys, vals, color=cols)
    for i, v in enumerate(vals):
        ax.text(v * 1.05, i, f"{v} B", va="center", fontsize=9)
    ax.axvline(DATA_PAYLOAD_LEN, color="k", ls="--", lw=1.4)
    ax.text(DATA_PAYLOAD_LEN * 1.03, -.6,
            f"DATA_PAYLOAD_LEN = {DATA_PAYLOAD_LEN} B", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("bytes (escala log)")
    ax.set_title("Fig. 3 - Por que o JSON canonico nao passa pelo radio: "
                 "tamanhos das representacoes")
    fig.tight_layout()
    fig.savefig(FIG / "fig3_tamanhos_payload.png", dpi=150)
    plt.close(fig)


def fig4_mesh(mesh, metrics):
    fig, axs = plt.subplots(1, 4, figsize=(16.5, 4))

    pdr = pd.Series(metrics["mesh"]["pdr_by_node"]).sort_values()
    star = pd.Series(metrics["star_baseline_hop_limit_1"]["pdr_by_node"])[pdr.index]
    y = np.arange(len(pdr))
    lbl = [" ".join(k.split()[1:])[:16] for k in pdr.index]
    axs[0].barh(y + .19, pdr.values, height=.36, color="#2e7d32",
                label="mesh (hop_limit=3)")
    axs[0].barh(y - .19, star.values, height=.36, color="#c62828",
                label="estrela (hop_limit=1, tipo LoRaWAN)")
    axs[0].set_yticks(y); axs[0].set_yticklabels(lbl, fontsize=8)
    axs[0].set_xlim(0, 108); axs[0].set_xlabel("PDR (%)")
    axs[0].legend(fontsize=7, loc="lower left")
    axs[0].set_title("PDR: mesh vs. estrela")

    d = mesh[mesh.delivered]
    axs[1].hist(d["hops"], bins=np.arange(.5, 4.5, 1), color="#1e88e5", ec="w")
    axs[1].set_xticks([1, 2, 3]); axs[1].set_xlabel("saltos ate o gateway")
    axs[1].set_title(f"Saltos (media {metrics['mesh']['mean_hops']})")

    axs[2].hist(d["latency_s"], bins=25, color="#6a1b9a", ec="w")
    axs[2].axvline(metrics["mesh"]["median_latency_s"], color="k", ls="--")
    axs[2].set_xlabel("latencia fim-a-fim (s)")
    axs[2].set_title(f"Latencia (mediana {metrics['mesh']['median_latency_s']} s)")

    toa = metrics["lora"]["toa_by_preset_s"]
    axs[3].bar(list(toa), list(toa.values()), color="#ef6c00")
    axs[3].set_ylabel("ToA (s)"); axs[3].tick_params(axis="x", rotation=35)
    axs[3].set_title("Tempo no ar por preset (23 B)")

    fig.suptitle("Fig. 4 - Desempenho da malha (managed flooding, hop_limit=3)")
    fig.tight_layout()
    fig.savefig(FIG / "fig4_metricas_mesh.png", dpi=150)
    plt.close(fig)


def fig5_risk(pl):
    piv = (pl.pivot_table(index="node_name", columns="timestamp",
                          values="risk_level", aggfunc="first")
           .reindex(sorted(pl.node_name.unique())))
    order = {"safe": 0, "attention": 1, "alert": 2, "critical": 3}
    m = piv.map(lambda v: order.get(v, np.nan)).to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(13, 3.4))
    cmap = matplotlib.colors.ListedColormap([C["safe"], C["attention"],
                                             C["alert"], C["critical"]])
    im = ax.imshow(m, aspect="auto", cmap=cmap, vmin=0, vmax=3,
                   interpolation="nearest")
    ax.set_yticks(range(len(piv.index)))
    ax.set_yticklabels([i[:26] for i in piv.index], fontsize=8)
    xt = np.linspace(0, m.shape[1] - 1, 9).astype(int)
    ax.set_xticks(xt)
    ax.set_xticklabels([pd.Timestamp(piv.columns[i]).strftime("%d/%m %Hh")
                        for i in xt], fontsize=8)
    cb = fig.colorbar(im, ticks=[0.4, 1.2, 1.9, 2.6])
    cb.ax.set_yticklabels(["safe", "attention", "alert", "critical"], fontsize=8)
    ax.set_title("Fig. 5 - Evolucao do risco por no (regra combinada: "
                 "cota AlertaBlu + taxa de variacao + chuva)")
    fig.tight_layout()
    fig.savefig(FIG / "fig5_timeline_risco.png", dpi=150)
    plt.close(fig)


def main():
    pl, mesh, topo, metrics, alerts = load()
    fig1_hydro(pl, alerts)
    fig2_topology(topo)
    fig3_sizes(metrics)
    fig4_mesh(mesh, metrics)
    fig5_risk(pl)
    for f in sorted(FIG.glob("*.png")):
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
