#!/usr/bin/env python3
"""
Executa o pipeline fim-a-fim e grava as evidencias em outputs/.

    python scripts/01_run_pipeline.py --start 2023-10-01 --end 2023-10-11
    python scripts/01_run_pipeline.py --offline      # sem rede (teste de fumaca)
"""
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atg_mesh.pipeline import run  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="dados sinteticos deterministas, sem acesso a rede")
    ap.add_argument("--start", default="2023-10-01")
    ap.add_argument("--end", default="2023-10-11")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", default=str(ROOT / "outputs"))
    ap.add_argument("--cache", default=str(ROOT / "data" / "raw"))
    a = ap.parse_args()

    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(outdir / "run.log", mode="w")],
    )
    run(offline=a.offline, start=a.start, end=a.end, outdir=outdir,
        cache=Path(a.cache), seed=a.seed)


if __name__ == "__main__":
    main()
