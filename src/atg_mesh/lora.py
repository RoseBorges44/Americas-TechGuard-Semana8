"""
Camada fisica LoRa: tempo no ar (ToA), orcamento de enlace e propagacao.

ToA segue a formulacao do datasheet Semtech SX1276 (secao 4.1.1.7), a mesma
usada pela calculadora oficial de LoRa e citada pela documentacao do Meshtastic
para derivar as taxas dos presets.

O modelo de propagacao e log-distancia calibrado no unico ponto medido em campo
disponivel na bibliografia obrigatoria: a referencia complementar
(arXiv:2605.20379) reporta enlace decodavel a 2,47 km com RSSI medio -110 dBm e
SNR +2,75 dB, TX 22 dBm, preset LongFast. Esse ponto fixa PL0 a 1 km.
"""
from __future__ import annotations

import math

from .config import (ACTIVE_PRESET, LOS_BONUS_DB, MESH_HEADER_BYTES,
                     MODEM_PRESETS, NOISE_FIGURE_DB, PATH_LOSS_EXPONENT,
                     PL0_DB, PL_D0_KM, PREAMBLE_SYMBOLS, SNR_LIMIT_DB,
                     TX_POWER_DBM)

FREQ_MHZ = 915.0


# ------------------------------------------------------------------ ToA
def time_on_air_s(payload_bytes: int, preset: str = ACTIVE_PRESET, *,
                  header: bool = True, crc: bool = True,
                  explicit_header: bool = True) -> float:
    bw, sf, cr = MODEM_PRESETS[preset]
    pl = payload_bytes + (MESH_HEADER_BYTES if header else 0)

    t_sym = (2 ** sf) / bw
    # Low Data Rate Optimize: obrigatorio quando t_sym > 16 ms
    de = 1 if (t_sym * 1000.0) > 16.0 else 0
    ih = 0 if explicit_header else 1
    crc_f = 1 if crc else 0

    num = 8 * pl - 4 * sf + 28 + 16 * crc_f - 20 * ih
    den = 4 * (sf - 2 * de)
    n_payload = 8 + max(math.ceil(num / den) * cr, 0)

    t_preamble = (PREAMBLE_SYMBOLS + 4.25) * t_sym
    return t_preamble + n_payload * t_sym


def bitrate_bps(preset: str = ACTIVE_PRESET) -> float:
    bw, sf, cr = MODEM_PRESETS[preset]
    return sf * (4.0 / cr) * (bw / (2 ** sf))


def duty_cycle_pct(payload_bytes: int, period_s: float,
                   preset: str = ACTIVE_PRESET) -> float:
    return 100.0 * time_on_air_s(payload_bytes, preset) / period_s


# ------------------------------------------------------------------ propagacao
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fspl_db(d_km: float) -> float:
    if d_km <= 0:
        return 0.0
    return 32.44 + 20 * math.log10(FREQ_MHZ) + 20 * math.log10(d_km)


def path_loss_db(d_km: float, *, los: bool = False, shadowing_db: float = 0.0) -> float:
    if d_km <= 0.01:
        d_km = 0.01
    pl = PL0_DB + 10 * PATH_LOSS_EXPONENT * math.log10(d_km / PL_D0_KM)
    if los:
        pl -= LOS_BONUS_DB
    return pl + shadowing_db


def noise_floor_dbm(preset: str = ACTIVE_PRESET) -> float:
    bw, _, _ = MODEM_PRESETS[preset]
    return -174.0 + 10 * math.log10(bw) + NOISE_FIGURE_DB


def link(tx_gain_dbi: float, rx_gain_dbi: float, d_km: float, *,
         los: bool = False, shadowing_db: float = 0.0,
         preset: str = ACTIVE_PRESET) -> dict:
    """Retorna RSSI, SNR, margem e se o enlace decodifica."""
    _, sf, _ = MODEM_PRESETS[preset]
    pl = path_loss_db(d_km, los=los, shadowing_db=shadowing_db)
    rssi = TX_POWER_DBM + tx_gain_dbi + rx_gain_dbi - pl
    snr = rssi - noise_floor_dbm(preset)
    limit = SNR_LIMIT_DB[sf]
    return {
        "distance_km": round(d_km, 3),
        "path_loss_db": round(pl, 1),
        "rssi_dbm": round(rssi, 1),
        "snr_db": round(snr, 1),
        "snr_limit_db": limit,
        "margin_db": round(snr - limit, 1),
        "decodable": snr >= limit,
    }
