"""
Geracao da mensagem de alerta curta, compativel com TEXT_MESSAGE_APP.

Restricoes de projeto:
- Limite duro de 200 caracteres (o payload util do Meshtastic e ~233 B; usamos
  200 para deixar folga ao cabecalho e a eventual sobrecarga de encapsulamento).
- ASCII puro: acentos viram 2 bytes em UTF-8, encurtando a mensagem util, e o
  firmware ja apresentou bug de mangling de UTF-8 no caminho JSON/MQTT
  (meshtastic/firmware#3562). Transliteramos para ASCII de proposito.
- Conteudo acionavel: o que, onde, quanto, tendencia, o que fazer, quem chamar.
"""
from __future__ import annotations

import unicodedata

from .config import COTA_PRIMEIRAS_VIAS_M

MAX_CHARS = 200

PREFIX = {
    "safe": "[ATG-BLU] NORMAL",
    "attention": "[ATG-BLU] ATENCAO",
    "alert": "[ATG-BLU] ALERTA",
    "critical": "[ATG-BLU] ALERTA MAXIMO",
}

ACTION = {
    "safe": "Sem acao. Monitoramento em curso.",
    "attention": "Acompanhe o AlertaBlu. Evite areas alagaveis.",
    "alert": "Evite as margens e retire bens de areas baixas.",
    "critical": "Deixe areas de risco AGORA. Procure ponto alto/abrigo.",
}


def to_ascii(text: str) -> str:
    return (unicodedata.normalize("NFKD", text)
            .encode("ascii", "ignore").decode("ascii"))


def _trend(rate: float | None, unit: str) -> str:
    if rate is None:
        return ""
    if rate > 0.01:
        return f" ({rate:+.2f}{unit}/h)"
    if rate < -0.01:
        return f" ({rate:+.2f}{unit}/h, recessao)"
    return " (estavel)"


def build_message(*, sensor_type: str, value: float, unit: str,
                  risk_level: str, site: str, rate: float | None,
                  accum_24h_mm: float | None, timestamp_local: str) -> str:
    head = PREFIX[risk_level]

    if sensor_type == "river_level":
        core = f"Rio Itajai-Acu {value:.2f}m{_trend(rate, 'm')} em {site}."
        ref = f" Cota 1as vias {COTA_PRIMEIRAS_VIAS_M:.1f}m."
    elif sensor_type == "rain_gauge":
        acc = f" 24h={accum_24h_mm:.0f}mm." if accum_24h_mm is not None else ""
        core = f"Chuva {value:.1f}mm/h em {site}.{acc}"
        ref = ""
    else:
        core = f"{sensor_type} {value:.2f}{unit} em {site}."
        ref = ""

    tail = f" {ACTION[risk_level]} Emerg 199/193. {timestamp_local}"
    msg = to_ascii(f"{head}: {core}{ref}{tail}")

    if len(msg) > MAX_CHARS:  # degradacao controlada, nunca truncar no meio
        msg = to_ascii(f"{head}: {core}{tail}")
    if len(msg) > MAX_CHARS:
        msg = to_ascii(f"{head}: {core} 199/193 {timestamp_local}")[:MAX_CHARS]
    return msg
