"""
Ingestao de dados AMBIENTAIS REAIS para Blumenau / Vale do Itajai.

Duas fontes abertas, sem chave de API, reprodutiveis por qualquer avaliador:

1) Chuva horaria observada  - Open-Meteo Archive API (reanalise ERA5-Land),
   amostrada nas coordenadas reais dos 4 nos pluviometricos da malha.
   https://archive-api.open-meteo.com/v1/archive

2) Vazao do rio (m3/s)      - Open-Meteo Flood API (GloFAS v4, Copernicus EMS),
   amostrada na regua fluviometrica de Blumenau (Prainha).
   https://flood-api.open-meteo.com/v1/flood

Da vazao para a COTA (metros de regua)
--------------------------------------
O GloFAS entrega vazao, nao cota. Nao existe curva-chave oficial publica da
estacao de Blumenau neste repositorio. Em vez de inventar uma, aplicamos um
mapeamento MONOTONICO de percentis da propria climatologia GloFAS (1994-hoje)
sobre a escada oficial de cotas do AlertaBlu:

    p50   -> 1,2 m   (nivel base tipico, dentro de "Normalidade")
    p90   -> 3,0 m   (limiar de "Observacao")
    p97   -> 4,0 m   (limiar de "Atencao")
    p99,3 -> 6,0 m   (limiar de "Alerta")
    p99,9 -> 8,0 m   (limiar de "Alerta Maximo")

Justificativa dos percentis: Blumenau entra em "Alerta" (6 m) poucas vezes por
ano (p99,3 ~ 2,5 dias/ano) e em "Alerta Maximo" (8 m) na ordem de uma vez a cada
1-3 anos (p99,9 ~ 0,37 dia/ano). ISSO E UMA APROXIMACAO DECLARADA, nao uma
curva-chave. Para operacao real seria necessario o par (vazao, cota) da estacao
83300000 da ANA / telemetria de 15 min do AlertaBlu. Ver README, "Limitacoes".
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BLUMENAU_GAUGE, RAIN_STATIONS

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

STAGE_ANCHORS = [(50.0, 1.2), (90.0, 3.0), (97.0, 4.0), (99.3, 6.0), (99.9, 8.0)]
CLIMATOLOGY_START = "1994-01-01"
RAIN_CLIM_YEARS = 6


def _get_json(url: str, params: dict, timeout: int = 60) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}",
                                 headers={"User-Agent": "ATG-P8/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------- coleta real
def fetch_rain(start: str, end: str) -> pd.DataFrame:
    """Chuva horaria (mm) nas 4 estacoes reais. Retorna long-format."""
    lats = ",".join(f"{lat:.4f}" for _, lat, _ in RAIN_STATIONS)
    lons = ",".join(f"{lon:.4f}" for _, _, lon in RAIN_STATIONS)
    data = _get_json(ARCHIVE_URL, {
        "latitude": lats, "longitude": lons,
        "start_date": start, "end_date": end,
        "hourly": "precipitation", "timezone": "UTC",
        "models": "era5_land",
    })
    blocks = data if isinstance(data, list) else [data]
    frames = []
    for (name, lat, lon), blk in zip(RAIN_STATIONS, blocks):
        h = blk["hourly"]
        frames.append(pd.DataFrame({
            "time": pd.to_datetime(h["time"], utc=True),
            "station": name, "lat": lat, "lon": lon,
            "rain_mm": pd.to_numeric(h["precipitation"], errors="coerce"),
        }))
    return pd.concat(frames, ignore_index=True)


def fetch_discharge(start: str, end: str,
                    coord: tuple[float, float] = BLUMENAU_GAUGE) -> pd.DataFrame:
    data = _get_json(FLOOD_URL, {
        "latitude": coord[0], "longitude": coord[1],
        "start_date": start, "end_date": end,
        "daily": "river_discharge",
    })
    d = data["daily"]
    return pd.DataFrame({
        "date": pd.to_datetime(d["time"], utc=True),
        "discharge_m3s": pd.to_numeric(d["river_discharge"], errors="coerce"),
    }).dropna()


def fetch_climatology(coord: tuple[float, float] = BLUMENAU_GAUGE) -> pd.DataFrame:
    end = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    return fetch_discharge(CLIMATOLOGY_START, end, coord)


def fetch_rain_climatology(years: int = RAIN_CLIM_YEARS) -> pd.DataFrame:
    """Chuva horaria nas 4 estacoes por N anos - base dos limiares percentilicos."""
    end = datetime.now(timezone.utc) - timedelta(days=10)
    start = end.replace(year=end.year - years)
    return fetch_rain(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


# ------------------------------------------------- vazao -> cota (aproximacao)
def build_rating(clim_discharge: np.ndarray) -> dict:
    q = np.asarray(clim_discharge, dtype=float)
    q = q[np.isfinite(q) & (q > 0)]
    pts = [(float(np.percentile(q, p)), h) for p, h in STAGE_ANCHORS]
    pts.sort()
    return {
        "anchors": [{"percentile": p, "discharge_m3s": round(qq, 1), "stage_m": h}
                    for (p, h), (qq, _) in zip(STAGE_ANCHORS, pts)],
        "q": [p[0] for p in pts],
        "h": [p[1] for p in pts],
        "n_days": int(q.size),
        "method": "mapeamento monotonico percentil->cota oficial AlertaBlu",
        "warning": "APROXIMACAO. Nao e a curva-chave oficial da estacao.",
    }


def discharge_to_stage(q_m3s, rating: dict) -> np.ndarray:
    qs = np.asarray(rating["q"], dtype=float)
    hs = np.asarray(rating["h"], dtype=float)
    q = np.asarray(q_m3s, dtype=float)
    # interpolacao em log(Q) -> comportamento de curva-chave potencia h = a*Q^b
    out = np.interp(np.log(np.maximum(q, 1e-3)), np.log(qs), hs)
    # extrapolacao acima do ultimo ancoradouro mantendo a inclinacao final
    slope = (hs[-1] - hs[-2]) / (math.log(qs[-1]) - math.log(qs[-2]))
    hi = q > qs[-1]
    out[hi] = hs[-1] + slope * (np.log(q[hi]) - math.log(qs[-1]))
    return np.maximum(out, 0.0)


def daily_to_hourly(df_daily: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Reamostra a serie diaria do GloFAS para passo horario (PCHIP monotonico)."""
    from scipy.interpolate import PchipInterpolator
    d = df_daily.dropna().sort_values("date").reset_index(drop=True)
    t = d["date"].astype("int64").to_numpy() / 1e9
    f = PchipInterpolator(t, d[value_col].to_numpy())
    idx = pd.date_range(d["date"].iloc[0], d["date"].iloc[-1], freq="h", tz="UTC")
    return pd.DataFrame({"time": idx,
                         value_col: f(idx.astype("int64").to_numpy() / 1e9)})


# ------------------------------------------------------- fallback determinista
def synthetic_event(hours: int = 96, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fallback offline (source='synthetic'). Reproduz a FORMA de uma cheia do
    Itajai-Acu: chuva convectiva no Alto Vale, resposta do rio com atraso de
    ~12 h e recessao lenta. NAO substitui a coleta real - serve apenas para que
    o pipeline seja executavel sem rede (teste de fumaca / CI).
    """
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
    idx = pd.date_range(t0, periods=hours, freq="h", tz="UTC")
    tt = np.arange(hours)

    # dois nucleos convectivos; acumulado de ~150-190 mm/24 h no Alto Vale,
    # ordem de grandeza compativel com as cheias historicas do Itajai-Acu
    burst = 13.0 * np.exp(-((tt - 20) ** 2) / (2 * 5.0 ** 2)) \
        + 8.0 * np.exp(-((tt - 38) ** 2) / (2 * 7.0 ** 2))
    frames = []
    for i, (name, lat, lon) in enumerate(RAIN_STATIONS):
        gain = [1.30, 1.10, 0.85, 0.95][i % 4]  # gradiente Alto -> Baixo Vale
        r = np.clip(burst * gain + rng.normal(0, 0.6, hours), 0, None)
        frames.append(pd.DataFrame({"time": idx, "station": name, "lat": lat,
                                    "lon": lon, "rain_mm": np.round(r, 2)}))
    rain = pd.concat(frames, ignore_index=True)

    # rio: convolucao da chuva media da bacia com hidrograma unitario exponencial
    mean_rain = rain.groupby("time")["rain_mm"].mean().to_numpy()
    k = np.exp(-np.arange(48) / 14.0)
    k /= k.sum()
    resp = np.convolve(mean_rain, k)[:hours]
    stage = 1.5 + 0.052 * np.cumsum(resp)          # subida
    stage -= 0.045 * np.maximum(tt - 58, 0)        # recessao lenta
    stage = np.clip(stage, 0.9, 8.6)
    river = pd.DataFrame({
        "time": idx,
        "stage_m": np.round(stage, 3),
        "discharge_m3s": np.round(38 * stage ** 1.9, 1),
    })
    return rain, river


# ------------------------------------------------------------------ orquestra
def load_event(*, offline: bool, start: str, end: str, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)

    if offline:
        rain, river = synthetic_event()
        return {"rain": rain, "river": river, "rating": None,
                "rain_climatology": rain,
                "clim_label": "serie SINTETICA do proprio evento (modo offline)",
                "source_rain": "synthetic", "source_river": "synthetic",
                "note": "modo offline - dados sinteticos deterministas"}

    rain = fetch_rain(start, end)
    clim = fetch_climatology()
    rating = build_rating(clim["discharge_m3s"].to_numpy())
    daily = fetch_discharge(start, end)
    hourly = daily_to_hourly(daily, "discharge_m3s")
    hourly["stage_m"] = np.round(discharge_to_stage(hourly["discharge_m3s"], rating), 3)

    clim_rain = fetch_rain_climatology()
    clim_rain.to_csv(cache / "rain_era5_climatology.csv", index=False)
    rain.to_csv(cache / "rain_era5_hourly.csv", index=False)
    daily.to_csv(cache / "discharge_glofas_daily.csv", index=False)
    hourly.to_csv(cache / "river_hourly_derived.csv", index=False)
    (cache / "rating_curve.json").write_text(json.dumps(rating, indent=2))
    clim.to_csv(cache / "discharge_glofas_climatology.csv", index=False)

    return {"rain": rain, "river": hourly, "rating": rating,
            "rain_climatology": clim_rain,
            "clim_label": (f"climatologia ERA5-Land de {RAIN_CLIM_YEARS} anos "
                           f"nas 4 estacoes ({len(clim_rain)} amostras horarias)"),
            "source_rain": "openmeteo_era5", "source_river": "glofas_openmeteo",
            "note": f"climatologia GloFAS: {rating['n_days']} dias diarios"}


def pick_peak_window(daily: pd.DataFrame, days: int = 5) -> tuple[str, str]:
    """Seleciona automaticamente a maior cheia da janela consultada."""
    i = int(daily["discharge_m3s"].idxmax())
    peak = daily.loc[i, "date"]
    a = (peak - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    b = (peak + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return a, b
