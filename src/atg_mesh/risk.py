"""
Motor de classificacao de risco.

Combina tres criterios, na linha do Algoritmo 1 de Zakaria et al. (2023), que
classifica a inundacao tanto pelo nivel absoluto quanto pela taxa de variacao:

  (A) Nivel absoluto do rio  -> escada OFICIAL da Defesa Civil de Blumenau
      (Normalidade / Observacao / Atencao / Alerta / Alerta Maximo).
      Diferenca em relacao ao artigo: Zakaria usa limiares arbitrarios de
      laboratorio (50/100/150 cm em um canal). Aqui os limiares sao os cotados
      oficialmente para o Rio Itajai-Acu em Blumenau (3/4/6/8 m).

  (B) Taxa de variacao (m/h) -> escalona o risco. O artigo mede "flood changing
      rate" em cm/min num canal urbano; num rio de grande porte a escala util e
      cm/h. Ancoragem documentada: na cheia de 04/05/2022 o AlertaBlu registrou
      subida media de ~25 cm/h em Blumenau.

  (C) Chuva (mm/1h e mm/24h) -> criterio independente. Os limiares sao derivados
      dos PERCENTIS da propria serie observada (ERA5-Land) e nao inventados.

O risco final e o MAXIMO entre (A escalonado por B) e (C).
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import (ALERTABLU_STAGE_LADDER, RAIN_FALLBACK, RATE_ESCALATE_1,
                     RATE_ESCALATE_2, RISK_ORDER)


@dataclass
class RainThresholds:
    h1_attention: float
    h1_alert: float
    h1_critical: float
    h24_attention: float
    h24_alert: float
    h24_critical: float
    provenance: str = "fallback"

    @classmethod
    def from_fallback(cls) -> "RainThresholds":
        return cls(**RAIN_FALLBACK, provenance="fallback (config.RAIN_FALLBACK)")

    @classmethod
    def from_series(cls, hourly_mm, accum24_mm,
                    label: str = "serie observada") -> "RainThresholds":
        """Deriva limiares dos percentis de uma CLIMATOLOGIA (sem chute)."""
        import numpy as np
        h = np.asarray([v for v in hourly_mm if v is not None], dtype=float)
        a = np.asarray([v for v in accum24_mm if v is not None], dtype=float)
        h_wet = h[h > 0.1]
        a_pos = a[a > 0.1]
        if h_wet.size < 50 or a_pos.size < 50:
            return cls.from_fallback()
        return cls(
            h1_attention=float(np.percentile(h_wet, 95)),
            h1_alert=float(np.percentile(h_wet, 99)),
            h1_critical=float(np.percentile(h_wet, 99.9)),
            h24_attention=float(np.percentile(a_pos, 95)),
            h24_alert=float(np.percentile(a_pos, 99)),
            h24_critical=float(np.percentile(a_pos, 99.9)),
            provenance=f"percentis p95/p99/p99.9 de {label}",
        )

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


@dataclass
class RiskAssessment:
    risk_level: str
    alertablu_stage: str | None
    driver: str            # o que dominou a decisao
    detail: str


def _idx(level: str) -> int:
    return RISK_ORDER.index(level)


def stage_from_level(level_m: float) -> tuple[str, str]:
    """Retorna (estagio_oficial_alertablu, risk_level) para um nivel em metros."""
    stage, risk = ALERTABLU_STAGE_LADDER[0][1], ALERTABLU_STAGE_LADDER[0][2]
    for lo, name, rl in ALERTABLU_STAGE_LADDER:
        if level_m >= lo:
            stage, risk = name, rl
    return stage, risk


def classify_river(level_m: float, rate_m_per_h: float | None) -> RiskAssessment:
    stage, base = stage_from_level(level_m)
    i = _idx(base)
    driver, detail = "nivel", f"nivel {level_m:.2f} m -> estagio '{stage}'"

    if rate_m_per_h is not None and rate_m_per_h > 0:
        bump = 0
        if rate_m_per_h >= RATE_ESCALATE_2:
            bump = 2
        elif rate_m_per_h >= RATE_ESCALATE_1:
            bump = 1
        if bump:
            i = min(i + bump, len(RISK_ORDER) - 1)
            driver = "nivel+taxa"
            detail += (f"; taxa {rate_m_per_h:+.2f} m/h escalona +{bump} "
                       f"(limiares {RATE_ESCALATE_1}/{RATE_ESCALATE_2} m/h)")
    elif rate_m_per_h is not None and rate_m_per_h < -0.05:
        detail += f"; rio em recessao ({rate_m_per_h:+.2f} m/h)"

    return RiskAssessment(RISK_ORDER[i], stage, driver, detail)


def classify_rain(rain_1h_mm: float, accum_24h_mm: float,
                  th: RainThresholds) -> RiskAssessment:
    """
    A chuva e um INDICADOR ANTECEDENTE, nao o perigo em si.

    Quem alaga Blumenau e o rio, nao a chuva caindo no telhado. Por isso a
    chuva sozinha eleva o risco no MAXIMO ate 'attention' - so a cota do
    Itajai-Acu (nivel absoluto + taxa de variacao) pode chegar a 'alert' ou
    'critical'.

    Motivo concreto (execucao de 12/07 sobre o evento de out/2023): com
    limiares percentilicos puros, o pipeline emitia
        "[ATG-BLU] ALERTA: Chuva 0.0mm/h em Garcia. 24h=54mm."
        "[ATG-BLU] ALERTA MAXIMO: Chuva 19.5mm/h em Garcia. 24h=26mm."
    ou seja, alerta com chuva ZERO caindo e alerta maximo com 26 mm em 24 h.
    Isso e ruido: soterra o sinal real, que era o rio subindo ate ~9,9 m.
    Um sistema que grita o tempo todo e um sistema que ninguem escuta.

    O percentil continua sendo o criterio (nada foi inventado); o que muda e o
    TETO que a chuva pode acionar sozinha.
    """
    triggered = (rain_1h_mm >= th.h1_attention
                 or accum_24h_mm >= th.h24_attention)
    level = "attention" if triggered else "safe"

    severity = ("moderada" if (rain_1h_mm >= th.h1_alert
                               or accum_24h_mm >= th.h24_alert) else "fraca")
    if rain_1h_mm >= th.h1_critical or accum_24h_mm >= th.h24_critical:
        severity = "intensa"

    detail = (f"chuva {rain_1h_mm:.1f} mm/1h, {accum_24h_mm:.1f} mm/24h "
              f"({severity}; limiar de atencao {th.h1_attention:.1f} mm/h ou "
              f"{th.h24_attention:.1f} mm/24h). Teto da chuva: 'attention' "
              f"- so a cota do rio escala acima disso.")
    return RiskAssessment(level, None, "chuva", detail)


def combine(*assessments: RiskAssessment) -> RiskAssessment:
    """Risco final = pior caso entre os criterios."""
    best = max(assessments, key=lambda a: _idx(a.risk_level))
    stage = next((a.alertablu_stage for a in assessments if a.alertablu_stage), None)
    return RiskAssessment(
        best.risk_level, stage, best.driver,
        " | ".join(a.detail for a in assessments),
    )
