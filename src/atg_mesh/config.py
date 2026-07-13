"""
Configuracao central do ATG-Mesh (Americas TechGuard - Periodo 8).

Reune: topologia real de nos em Blumenau/SC, parametros de radio Meshtastic
(regiao ANZ 915 MHz, preset LongFast) e os limiares oficiais de nivel do rio
publicados pela Defesa Civil de Blumenau (AlertaBlu).

Todas as constantes aqui sao rastreaveis a uma fonte documentada no README.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# --------------------------------------------------------------------------
# 1) Limiares oficiais de nivel do Rio Itajai-Acu em Blumenau (AlertaBlu)
#    Fonte: Defesa Civil de Blumenau, "Criterios de Nivel do Rio".
#    Normalidade 0-3 m | Observacao 3-4 m | Atencao 4-6 m | Alerta 6-8 m
#    | Alerta Maximo > 8 m
# --------------------------------------------------------------------------
ALERTABLU_STAGE_LADDER = [
    # (limite_inferior_m, nome_oficial, risk_level do payload ATG-ENV)
    (0.0, "normalidade", "safe"),
    (3.0, "observacao", "attention"),
    (4.0, "atencao", "attention"),
    (6.0, "alerta", "alert"),
    (8.0, "alerta_maximo", "critical"),
]

RISK_ORDER = ["safe", "attention", "alert", "critical"]

# Taxa de variacao do nivel (m/h). Ancora documentada: durante a cheia de
# 04/05/2022 o AlertaBlu registrou subida media de ~25 cm/h em Blumenau.
RATE_ESCALATE_1 = 0.25   # m/h  -> sobe 1 degrau de risco
RATE_ESCALATE_2 = 0.40   # m/h  -> sobe 2 degraus de risco

# Limiares de chuva usados apenas como FALLBACK offline. No modo padrao eles
# sao recalculados a partir dos percentis da serie observada real (ERA5-Land).
RAIN_FALLBACK = {
    "h1_attention": 20.0, "h1_alert": 35.0, "h1_critical": 50.0,     # mm/1h
    "h24_attention": 60.0, "h24_alert": 100.0, "h24_critical": 150.0,  # mm/24h
}

# Cotas de inundacao urbana usadas na mensagem de alerta (referencia AlertaBlu
# "Cotas de Enchente": as primeiras vias de Blumenau comecam a ser atingidas a
# partir de ~7,4 m no marco da Prainha).
COTA_PRIMEIRAS_VIAS_M = 7.4

# --------------------------------------------------------------------------
# 2) Radio - Meshtastic
#    Regiao: Brasil aparece na tabela oficial do Meshtastic como "ANZ | BR_902".
#    BR_902 opera em 902-907,5 MHz. Como a atividade exige 915 MHz obrigatorio,
#    a regiao correta e ANZ (915-928 MHz). Ver README, secao "Decisoes tecnicas".
#    Preset LongFast (padrao Meshtastic): BW 250 kHz, SF 11, CR 4/5.
# --------------------------------------------------------------------------
MESHTASTIC_REGION = "ANZ"
MESH_CHANNEL = "ATG-Blumenau"   # canal primario (uplink/downlink habilitados)
MESH_DOWNLINK_CHANNEL = "mqtt"  # canal exigido pelo firmware p/ downlink JSON
MQTT_ROOT = "msh/BR"

MODEM_PRESETS = {
    # nome:        (bw_hz,   sf, cr_denom)
    "LONG_FAST":   (250_000, 11, 5),
    "LONG_SLOW":   (125_000, 12, 5),
    "MEDIUM_FAST": (250_000, 9, 5),
    "SHORT_FAST":  (250_000, 7, 5),
}
ACTIVE_PRESET = "LONG_FAST"

PREAMBLE_SYMBOLS = 16      # Meshtastic usa preambulo 16 (docs "Overview")
MESH_HEADER_BYTES = 16     # cabecalho fora do Data protobuf
DATA_PAYLOAD_LEN = 233     # limite de bytes do campo Data.payload (firmware)
MAX_LORA_PACKET = 256      # limite fisico do pacote LoRa

TX_POWER_DBM = 22.0
NOISE_FIGURE_DB = 6.0

# SNR minimo de demodulacao por SF (Semtech SX127x/SX126x, valores tipicos)
SNR_LIMIT_DB = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}

HOP_LIMIT = 3              # padrao Meshtastic

# Modelo de propagacao log-distancia.
#   PL(d) = PL0 + 10*n*log10(d/1km) [+ sombreamento] [- bonus de LOS]
# PL0 NAO foi ajustado aos dados: e a perda de espaco livre a 1 km em 915 MHz
# (FSPL = 91,7 dB) somada a 30 dB de perda em excesso, valor tabelado para
# ambiente urbano denso/vegetado. n = 3,5 (NLOS urbano com relevo).
#
# O modelo e entao VALIDADO (nao calibrado) contra a unica medida de campo
# disponivel na bibliografia obrigatoria - referencia complementar
# (arXiv:2605.20379), enlace de 2,47 km, TX 22 dBm, antenas stock (~2 dBi):
#     modelo  -> RSSI = 26 - (121,7 + 35*log10(2,47)) = -109,4 dBm
#     medido  -> RSSI medio = -110 dBm   (erro 0,6 dB)
# Ver tests/test_atg.py::test_calibracao_do_modelo_de_propagacao.
PL_D0_KM = 1.0
PL0_DB = 121.7             # FSPL(1 km, 915 MHz) = 91,7 dB + 30 dB de excesso
PATH_LOSS_EXPONENT = 3.5   # NLOS urbano/vegetado do Vale do Itajai
SHADOWING_SIGMA_DB = 6.0
LOS_BONUS_DB = 12.0        # ganho quando ambos os nos estao em ponto elevado

# --------------------------------------------------------------------------
# 3) Topologia: nos reais em Blumenau / Vale do Itajai
# --------------------------------------------------------------------------
SensorType = Literal["rain_gauge", "river_level", "repeater", "gateway"]


@dataclass(frozen=True)
class MeshNode:
    node_id: str            # !hex (padrao Meshtastic User ID)
    node_num: int           # equivalente decimal (campo "from" do JSON MQTT)
    name: str
    role: str               # CLIENT | ROUTER | GATEWAY
    sensor_type: SensorType
    lat: float
    lon: float
    alt_m: float
    antenna_dbi: float
    elevated: bool = False  # ponto alto -> bonus de LOS
    hardware: str = "Heltec LoRa 32 V3 (ESP32 + SX1276 + OLED)"
    battery_pct: int = 100
    site: str = ""


def _num(hex_id: str) -> int:
    return int(hex_id.lstrip("!"), 16)


NODES: list[MeshNode] = [
    MeshNode("!a76c0001", _num("a76c0001"), "ATG-BLU-01 Vila Itoupava",
             "CLIENT", "rain_gauge", -26.7550, -49.0906, 30, 5.0,
             hardware="LILYGO TTGO T-Beam V1.1 (ESP32 + SX1276 + GPS)",
             site="Vila Itoupava"),
    MeshNode("!a76c0002", _num("a76c0002"), "ATG-BLU-02 Itoupava Central",
             "ROUTER", "rain_gauge", -26.8331, -49.1024, 120, 5.0, elevated=True,
             site="Itoupava Central"),
    MeshNode("!a76c0003", _num("a76c0003"), "ATG-BLU-03 Morro do Aipim (repetidor)",
             "ROUTER", "repeater", -26.9047, -49.0872, 250, 8.0, elevated=True,
             site="Morro do Aipim"),
    MeshNode("!a76c0004", _num("a76c0004"), "ATG-BLU-04 Garcia",
             "CLIENT", "rain_gauge", -26.9401, -49.0678, 20, 5.0, site="Garcia"),
    MeshNode("!a76c0005", _num("a76c0005"), "ATG-BLU-05 Velha",
             "CLIENT", "rain_gauge", -26.9219, -49.1043, 20, 5.0, site="Velha"),
    MeshNode("!a76c0006", _num("a76c0006"), "ATG-BLU-06 Prainha (regua fluviometrica)",
             "CLIENT", "river_level", -26.9187, -49.0665, 12, 5.0,
             hardware="LILYGO TTGO T-Beam V1.1 + sensor ultrassonico HC-SR04",
             site="Prainha"),
    MeshNode("!a76c00ff", _num("a76c00ff"), "ATG-BLU-GW Defesa Civil",
             "GATEWAY", "gateway", -26.9194, -49.0661, 20, 8.0, elevated=True,
             hardware="LILYGO TTGO LoRa T3S3 1.2 + backhaul Wi-Fi",
             site="Centro / Defesa Civil"),
]

GATEWAY = NODES[-1]
NODE_BY_ID = {n.node_id: n for n in NODES}
NODE_BY_NUM = {n.node_num: n for n in NODES}

# Coordenada de referencia hidrologica (regua de Blumenau / Prainha)
BLUMENAU_GAUGE = (-26.9187, -49.0665)

# Estacoes pluviometricas (coordenadas dos nos de chuva) usadas na coleta real
RAIN_STATIONS = [(n.name, n.lat, n.lon) for n in NODES if n.sensor_type == "rain_gauge"]

# Codigos compactos usados no formato de fio ATG-C1
SENSOR_CODE = {"river_level": 1, "rain_gauge": 2, "repeater": 3, "gateway": 4,
               "river_discharge": 5}
SENSOR_CODE_INV = {v: k for k, v in SENSOR_CODE.items()}
RISK_CODE = {"safe": 0, "attention": 1, "alert": 2, "critical": 3}
RISK_CODE_INV = {v: k for k, v in RISK_CODE.items()}

UNITS = {"river_level": "m", "rain_gauge": "mm", "river_discharge": "m3/s"}
