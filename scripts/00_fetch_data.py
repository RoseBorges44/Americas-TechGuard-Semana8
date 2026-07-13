#!/usr/bin/env python3
"""
Coleta os dados AMBIENTAIS REAIS (Open-Meteo ERA5-Land + GloFAS) e escolhe
automaticamente a maior cheia da janela pedida - sem cherry-picking manual.

    python scripts/00_fetch_data.py --from 2023-01-01 --to 2025-12-31
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atg_mesh import ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="a", default="2023-01-01")
    ap.add_argument("--to", dest="b", default="2025-12-31")
    ap.add_argument("--days", type=int, default=5,
                    help="metade da janela do evento, em dias")
    args = ap.parse_args()

    cache = ROOT / "data" / "raw"
    cache.mkdir(parents=True, exist_ok=True)

    print(f"[1/2] GloFAS (Copernicus EMS): vazao diaria {args.a} .. {args.b}")
    daily = ingest.fetch_discharge(args.a, args.b)
    i = int(daily["discharge_m3s"].idxmax())
    peak_q = float(daily.loc[i, "discharge_m3s"])
    peak_d = str(daily.loc[i, "date"].date())
    print(f"      pico = {peak_q:.0f} m3/s em {peak_d}")

    start, end = ingest.pick_peak_window(daily, days=args.days)
    print(f"[2/2] janela do evento selecionada automaticamente: {start} .. {end}")

    (cache / "event_window.json").write_text(json.dumps(
        {"query": [args.a, args.b], "start": start, "end": end,
         "peak_date": peak_d, "peak_discharge_m3s": peak_q}, indent=2))

    ingest.load_event(offline=False, start=start, end=end, cache=cache)
    print(f"      arquivos gravados em {cache}")
    for f in sorted(cache.glob("*")):
        print(f"        - {f.name} ({f.stat().st_size/1024:.1f} kB)")
    print("\nProximo passo:")
    print(f"  python scripts/01_run_pipeline.py --start {start} --end {end}")


if __name__ == "__main__":
    main()
