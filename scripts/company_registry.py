"""
scripts/company_registry.py

ONE registry of the companies this project loads: data/companies.yaml. The historical downloader
(download_historical.COMPANIES: key -> (LEI, name)) and loader (load_historical.COMPANY_MAP: key -> (name, currency,
sector, country)) used to keep hand-written copies of it; adding the first five universe companies meant editing
three files that had to agree (tests/test_historical_registry.py was written to catch the drift). Both are now
built from here, so a company is added in one place.

An entry takes part in the historical depth only when it has `key` (the filename prefix of its
data/raw/historical/<key>_<period-end>.zip packages - lower case letters and underscores) and `lei`.
"""
import re
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).parent.parent / "data" / "companies.yaml"
KEY_RE = re.compile(r"^[a-z_]+$")
LEI_RE = re.compile(r"^[A-Z0-9]{20}$")


def load_entries(path: Path = REGISTRY_PATH) -> dict:
    """{zip stem: entry} exactly as data/companies.yaml has them."""
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("companies", {}) or {}


def historical_companies(path: Path = REGISTRY_PATH) -> dict:
    """{key: entry} for the entries with a `key` - checked: well-formed and unique keys and LEIs."""
    out, leis = {}, {}
    for stem, e in load_entries(path).items():
        key = e.get("key")
        if not key:
            continue
        if not KEY_RE.match(key):
            raise ValueError(f"{stem}: key {key!r} must be lower-case letters and underscores (the loader's filename rule)")
        if not LEI_RE.match(str(e.get("lei", ""))):
            raise ValueError(f"{stem}: lei {e.get('lei')!r} is not a 20-character LEI")
        if key in out:
            raise ValueError(f"key {key!r} is used twice ({stem})")
        if e["lei"] in leis:
            raise ValueError(f"LEI {e['lei']} is used by both {leis[e['lei']]} and {stem}")
        out[key], leis[e["lei"]] = e, stem
    return out
