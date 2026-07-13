"""
Testes do ATG-Mesh.  Executar:  python -m pytest -q   (a partir da raiz do repo)
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atg_mesh import alert, codec, lora, risk  # noqa: E402
from atg_mesh.config import DATA_PAYLOAD_LEN, MAX_LORA_PACKET  # noqa: E402
from atg_mesh.mesh_sim import MeshNetwork  # noqa: E402
from atg_mesh.meshtastic_json import (downlink_sendtext, hex_to_num,  # noqa: E402
                                      num_to_hex, uplink_telemetry)
from atg_mesh.schema import EXAMPLE  # noqa: E402
from atg_mesh.validator import parse_and_validate  # noqa: E402


# ---------------------------------------------------------------- schema
def test_exemplo_canonico_e_valido():
    assert parse_and_validate(json.dumps(EXAMPLE)).ok


@pytest.mark.parametrize("mutate,expect", [
    (lambda p: p.pop("sensor_value"), "campo minimo ausente"),
    (lambda p: p.update(risk_level="panic"), "enum de risco invalido"),
    (lambda p: p.update(timestamp="12/07/2026 14:00"), "timestamp nao-ISO"),
    (lambda p: p.update(latitude=48.85), "fora da bbox do Vale do Itajai"),
    (lambda p: p.update(sensor_value=999.0), "nivel implausivel"),
    (lambda p: p.update(unit="mm"), "unidade incoerente com o sensor"),
    (lambda p: p.update(device_id="a76c0006"), "device_id sem '!'"),
])
def test_payloads_invalidos_sao_rejeitados(mutate, expect):
    p = json.loads(json.dumps(EXAMPLE))
    mutate(p)
    res = parse_and_validate(json.dumps(p))
    assert not res.ok, expect
    assert res.errors


def test_json_malformado():
    res = parse_and_validate('{"schema": "atg-env/1.0", ')
    assert not res.ok and "malformado" in res.errors[0]


# ---------------------------------------------------------------- codec
def test_codec_bin_roundtrip():
    b = codec.to_c1_bin(EXAMPLE)
    assert len(b) == 23
    d = codec.from_c1_bin(b)
    assert d["node_num"] == EXAMPLE["node_num"]
    assert abs(d["sensor_value"] - EXAMPLE["sensor_value"]) < 0.01
    assert abs(d["latitude"] - EXAMPLE["latitude"]) < 1e-5
    assert d["risk_level"] == EXAMPLE["risk_level"]
    assert abs(d["rate_of_change"] - EXAMPLE["rate_of_change"]) < 1e-3


def test_codec_json_roundtrip():
    d = codec.from_c1_json(codec.to_c1_json(EXAMPLE))
    assert d["sensor_type"] == EXAMPLE["sensor_type"]
    assert abs(d["sensor_value"] - EXAMPLE["sensor_value"]) < 0.01


def test_todas_as_formas_de_fio_cabem_no_meshtastic():
    s = codec.size_report(EXAMPLE)
    assert s["ATG-C1-BIN (bytes crus, PRIVATE_APP)"] <= DATA_PAYLOAD_LEN
    assert s["ATG-C1-B64 (dentro de TEXT_MESSAGE_APP)"] <= DATA_PAYLOAD_LEN
    assert s["ATG-C1-JSON"] <= DATA_PAYLOAD_LEN
    # e a razao de existir do codec: o canonico NAO cabe
    assert s["atg-env/1.0 (JSON minificado)"] > DATA_PAYLOAD_LEN


# ---------------------------------------------------------------- risco
@pytest.mark.parametrize("nivel,estagio,risco", [
    (1.8, "normalidade", "safe"),
    (3.4, "observacao", "attention"),
    (5.1, "atencao", "attention"),
    (6.7, "alerta", "alert"),
    (8.9, "alerta_maximo", "critical"),
])
def test_escada_oficial_alertablu(nivel, estagio, risco):
    a = risk.classify_river(nivel, rate_m_per_h=0.0)
    assert (a.alertablu_stage, a.risk_level) == (estagio, risco)


def test_taxa_de_variacao_escalona_o_risco():
    lento = risk.classify_river(5.0, 0.05)     # atencao
    rapido = risk.classify_river(5.0, 0.30)    # +1 degrau
    muito = risk.classify_river(5.0, 0.55)     # +2 degraus
    assert lento.risk_level == "attention"
    assert rapido.risk_level == "alert"
    assert muito.risk_level == "critical"


def test_risco_final_e_o_pior_caso():
    th = risk.RainThresholds.from_fallback()
    a = risk.classify_river(1.0, 0.0)                  # safe
    b = risk.classify_rain(60.0, 200.0, th)            # critical
    assert risk.combine(a, b).risk_level == "critical"


# ---------------------------------------------------------------- alerta
def test_mensagem_cabe_no_limite_e_e_ascii():
    m = alert.build_message(sensor_type="river_level", value=8.42, unit="m",
                            risk_level="critical", site="Prainha", rate=0.51,
                            accum_24h_mm=180.0, timestamp_local="12/07 14:00")
    assert len(m) <= alert.MAX_CHARS
    assert m.isascii()
    assert len(m.encode()) <= DATA_PAYLOAD_LEN


# ---------------------------------------------------------------- LoRa
def test_time_on_air_coerente_com_o_preset():
    t_lf = lora.time_on_air_s(23, "LONG_FAST")
    t_ls = lora.time_on_air_s(23, "LONG_SLOW")
    t_sf = lora.time_on_air_s(23, "SHORT_FAST")
    assert t_sf < t_lf < t_ls          # mais SF/menos BW -> mais tempo no ar
    assert 0.3 < t_lf < 1.0            # ordem de grandeza esperada
    assert 900 < lora.bitrate_bps("LONG_FAST") < 1200   # ~1 kbps (docs oficiais)


def test_calibracao_do_modelo_de_propagacao():
    """
    VALIDACAO do modelo de propagacao contra a medida de campo da referencia
    complementar (arXiv:2605.20379): 2,47 km, TX 22 dBm, antenas stock (~2 dBi),
    RSSI medio medido = -110 dBm. O modelo nao foi ajustado a esse ponto.
    """
    q = lora.link(2.0, 2.0, 2.47, los=False)
    assert abs(q["rssi_dbm"] - (-110.0)) <= 5.0, q
    assert q["decodable"]


def test_pacote_nunca_excede_o_limite_fisico():
    b = codec.to_c1_bin(EXAMPLE)
    assert len(b) + 16 <= MAX_LORA_PACKET


# ---------------------------------------------------------------- malha
def test_entrega_multi_salto_do_no_mais_distante():
    net = MeshNetwork(seed=1)
    r = net.send("!a76c0001", 23, packet_id=1)   # Vila Itoupava (18 km do GW)
    assert r.delivered
    assert r.hops >= 2, "o no mais ao norte nao alcanca o gateway em 1 salto"
    assert r.hops <= 3, "excedeu o hop_limit padrao do Meshtastic"


def test_cache_de_duplicatas_limita_retransmissoes():
    net = MeshNetwork(seed=1)
    r = net.send("!a76c0006", 23, packet_id=2)
    relays = [t.relay for t in r.transmissions]
    assert len(relays) == len(set(relays)), "um no retransmitiu duas vezes"


# ---------------------------------------------------------------- Meshtastic JSON
def test_conversao_de_node_id():
    assert hex_to_num("!7efeee00") == 2130636288      # exemplo da doc oficial
    assert num_to_hex(2130636288) == "!7efeee00"


def test_envelope_uplink_tem_os_campos_da_doc():
    env = uplink_telemetry(EXAMPLE, rssi=-98, snr=8.1, hops=1)
    for k in ("channel", "from", "id", "payload", "sender", "timestamp",
              "to", "type"):
        assert k in env
    assert env["type"] == "telemetry"
    assert env["to"] == 4294967295


def test_downlink_tem_os_campos_obrigatorios():
    dl = downlink_sendtext("teste")
    assert "from" in dl and "payload" in dl      # exigidos pelo firmware
    assert dl["type"] == "sendtext"
