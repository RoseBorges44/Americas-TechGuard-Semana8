"""
Parser + validador do payload ATG-ENV 1.0.

Trata os erros basicos exigidos pela ETAPA 3: JSON malformado, campo ausente,
tipo errado, valor fora de faixa, timestamp invalido e coordenada fora da
bounding box do Vale do Itajai (guarda contra no mal configurado / spoofing).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

from .schema import ATG_ENV_SCHEMA

_VALIDATOR = Draft202012Validator(ATG_ENV_SCHEMA)

# Bounding box generosa do Vale do Itajai (SC)
BBOX = {"lat_min": -27.6, "lat_max": -26.3, "lon_min": -50.2, "lon_max": -48.4}

# Faixas plausiveis por tipo de sensor (guarda contra leitura absurda)
PLAUSIBLE = {
    "river_level": (-0.5, 20.0),      # cheia recorde de 1911 em Blumenau: 16,90 m
    "rain_gauge": (0.0, 200.0),       # mm/h
    "river_discharge": (0.0, 10000.0),
    "repeater": (-1e9, 1e9),
    "gateway": (-1e9, 1e9),
}


@dataclass
class ValidationResult:
    ok: bool
    payload: dict | None
    errors: list[str]

    def __bool__(self) -> bool:  # permite `if result:`
        return self.ok


def parse_and_validate(raw: str | bytes | dict) -> ValidationResult:
    errors: list[str] = []

    # 1) Desserializacao
    if isinstance(raw, dict):
        obj = raw
    else:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            return ValidationResult(False, None, [f"JSON malformado: {exc}"])

    if not isinstance(obj, dict):
        return ValidationResult(False, None, ["payload nao e um objeto JSON"])

    # 2) Schema (campos minimos, tipos, enums, tamanhos)
    for err in sorted(_VALIDATOR.iter_errors(obj), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "(raiz)"
        errors.append(f"schema[{path}]: {err.message}")

    if errors:
        return ValidationResult(False, None, errors)

    # 3) Regras semanticas que o JSON Schema nao cobre
    try:
        ts = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            errors.append("timestamp sem timezone (exigido ISO 8601 com offset)")
        else:
            obj["_ts"] = ts.astimezone(timezone.utc)
    except ValueError as exc:
        errors.append(f"timestamp invalido: {exc}")

    lat, lon = obj["latitude"], obj["longitude"]
    if not (BBOX["lat_min"] <= lat <= BBOX["lat_max"]
            and BBOX["lon_min"] <= lon <= BBOX["lon_max"]):
        errors.append(f"coordenada fora da bbox do Vale do Itajai: ({lat}, {lon})")

    lo, hi = PLAUSIBLE[obj["sensor_type"]]
    if not (lo <= obj["sensor_value"] <= hi):
        errors.append(
            f"sensor_value {obj['sensor_value']} fora da faixa plausivel "
            f"[{lo}, {hi}] para {obj['sensor_type']}"
        )

    expected_unit = {"river_level": "m", "rain_gauge": "mm",
                     "river_discharge": "m3/s"}.get(obj["sensor_type"])
    if expected_unit and obj["unit"] != expected_unit:
        errors.append(f"unit '{obj['unit']}' incoerente com sensor_type "
                      f"'{obj['sensor_type']}' (esperado '{expected_unit}')")

    if errors:
        return ValidationResult(False, None, errors)

    obj.pop("_ts", None)
    return ValidationResult(True, obj, [])
