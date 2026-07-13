"""
Simulador da rede Meshtastic (managed flooding).

Reproduz, em software, o comportamento descrito na documentacao oficial do
Meshtastic e caracterizado experimentalmente pela referencia complementar
(arXiv:2605.20379):

  - inundacao gerenciada (managed flooding): todo no que recebe um pacote pode
    retransmiti-lo;
  - cache de IDs de pacote: duplicatas sao descartadas (limita a explosao);
  - back-off aleatorio inversamente proporcional ao SNR recebido: quem ouviu
    melhor retransmite antes (e quem ouviu pior costuma desistir ao ouvir a
    retransmissao alheia);
  - hop_limit (padrao 3), decrementado a cada retransmissao;
  - CSMA/CA simplificado: o meio e ocupado durante o ToA do pacote.

Metricas de saida: PDR, numero de saltos, latencia fim-a-fim, RSSI/SNR por
enlace e ocupacao de canal (airtime) - as mesmas grandezas avaliadas por
Zakaria et al. (2023) e pela referencia complementar.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import (ACTIVE_PRESET, GATEWAY, HOP_LIMIT, MODEM_PRESETS, NODES,
                     SHADOWING_SIGMA_DB, SNR_LIMIT_DB)
from .lora import haversine_km, link, time_on_air_s


@dataclass
class Reception:
    node_id: str
    rssi_dbm: float
    snr_db: float
    hops: int
    delivered_at_s: float


@dataclass
class TxRecord:
    packet_id: int
    origin: str
    relay: str
    hop_left: int
    airtime_s: float


@dataclass
class MeshResult:
    packet_id: int
    origin: str
    delivered: bool
    hops: int | None
    latency_s: float | None
    rssi_dbm: float | None
    snr_db: float | None
    path: list[str] = field(default_factory=list)
    transmissions: list[TxRecord] = field(default_factory=list)
    total_airtime_s: float = 0.0


class MeshNetwork:
    def __init__(self, nodes=None, preset: str = ACTIVE_PRESET, seed: int = 42,
                 hop_limit: int = HOP_LIMIT):
        self.nodes = {n.node_id: n for n in (nodes or NODES)}
        self.preset = preset
        self.rng = random.Random(seed)
        self.gateway_id = GATEWAY.node_id
        self._sf = MODEM_PRESETS[preset][1]
        self.hop_limit = hop_limit   # 1 = topologia estrela (equivalente LoRaWAN)

    # ---------------------------------------------------------------- enlaces
    def link_quality(self, a_id: str, b_id: str, *, fading: bool = True) -> dict:
        a, b = self.nodes[a_id], self.nodes[b_id]
        d = haversine_km((a.lat, a.lon), (b.lat, b.lon))
        los = a.elevated and b.elevated
        sh = self.rng.gauss(0, SHADOWING_SIGMA_DB) if fading else 0.0
        return link(a.antenna_dbi, b.antenna_dbi, d, los=los,
                    shadowing_db=sh, preset=self.preset)

    def topology(self) -> list[dict]:
        """Matriz de enlaces sem desvanecimento (documentacao/figura)."""
        out = []
        ids = list(self.nodes)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                q = self.link_quality(a, b, fading=False)
                out.append({"a": a, "b": b,
                            "a_name": self.nodes[a].name,
                            "b_name": self.nodes[b].name, **q})
        return out

    # ---------------------------------------------------------------- flooding
    def send(self, origin_id: str, payload_bytes: int,
             packet_id: int) -> MeshResult:
        toa = time_on_air_s(payload_bytes, self.preset)
        res = MeshResult(packet_id, origin_id, False, None, None, None, None)

        seen: set[str] = set()          # cache de ID de pacote por no
        # fila de eventos: (tempo, relay_id, hop_left, snr_no_relay)
        queue: list[tuple[float, str, int, float]] = [(0.0, origin_id, self.hop_limit, 99.0)]
        channel_busy_until = 0.0
        best: Reception | None = None
        path_parent: dict[str, str] = {}

        while queue:
            queue.sort(key=lambda e: e[0])
            t, relay, hop_left, _snr = queue.pop(0)
            if relay in seen or hop_left <= 0:
                continue
            seen.add(relay)

            # CSMA/CA simplificado: espera o canal liberar
            t_tx = max(t, channel_busy_until)
            channel_busy_until = t_tx + toa
            res.transmissions.append(
                TxRecord(packet_id, origin_id, relay, hop_left, toa))
            res.total_airtime_s += toa
            t_rx = t_tx + toa

            for other in self.nodes:
                if other == relay or other in seen:
                    continue
                q = self.link_quality(relay, other)
                if not q["decodable"]:
                    continue
                hops_used = self.hop_limit - hop_left + 1

                if other == self.gateway_id:
                    if best is None or t_rx < best.delivered_at_s:
                        best = Reception(other, q["rssi_dbm"], q["snr_db"],
                                         hops_used, t_rx)
                        path_parent[other] = relay
                    continue

                if hop_left - 1 > 0:
                    # back-off inversamente proporcional ao SNR (Meshtastic)
                    margin = max(q["snr_db"] - SNR_LIMIT_DB[self._sf], 0.0)
                    backoff = toa * (1.0 + 2.0 * (1.0 - min(margin / 25.0, 1.0)))
                    backoff += self.rng.uniform(0, 0.2 * toa)
                    path_parent.setdefault(other, relay)
                    queue.append((t_rx + backoff, other, hop_left - 1, q["snr_db"]))

        if best:
            res.delivered = True
            res.hops = best.hops
            res.latency_s = round(best.delivered_at_s, 3)
            res.rssi_dbm = best.rssi_dbm
            res.snr_db = best.snr_db
            # reconstroi caminho
            path, cur = [self.gateway_id], self.gateway_id
            while cur in path_parent and path_parent[cur] != cur:
                cur = path_parent[cur]
                path.append(cur)
                if cur == origin_id or len(path) > 8:
                    break
            res.path = list(reversed(path))
        return res
