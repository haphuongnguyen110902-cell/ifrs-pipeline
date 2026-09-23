"""
scripts/download_bdif.py

French issuers' annual reports (ESEF packages) from the AMF's BDIF - the regulator's own database, where every
Document d'enregistrement universel is filed. filings.xbrl.org, this project's first source, lags badly for French
filers (checked 2026-09-23: no FY2025 for LVMH, Danone or EssilorLuxottica; nothing after FY2023 for Kering), which is
why the comps table compared FY2025 companies with FY2024 and FY2023 ones.

Safety, same rules as download_historical.py:
- the issuer must match exactly (a text search for "Danone" also returns GENERIX GROUP);
- BDIF publishes no checksum (the 64-hex name in its document path is NOT the file's SHA-256 - checked on 5 files,
  none matched), so a file is kept only if every byte announced arrived and every member passes the zip's own CRC
  check; its SHA-256 is printed for provenance. The stronger check comes after loading: the prior-year comparatives
  inside a new report must equal the figures already loaded from that year's own report;
- the fiscal year is read from the package itself (its report is named <LEI>-<period end>) and the LEI must be the
  issuer's own - never BDIF's dates (Danone's FY2023 filing carries dateAction 2023-03-12, a year early);
- the download refuses to leave less than --min-free-mb on disk; nothing partial is ever kept.
Files are named like load_historical.py expects: data/raw/historical/<key>_<YYYY-MM-DD>.zip.

    python scripts/download_bdif.py --only lvmh kering --dry-run
    python scripts/download_bdif.py --only lvmh kering --skip-loaded
"""
import argparse
import datetime
import hashlib
import importlib.util
import shutil
import sys
import zipfile
import zlib
from pathlib import Path

import requests

API = "https://bdif.amf-france.org/back/api/v1"

# key (filename prefix load_historical.py reads) -> (issuer as BDIF names it, LEI, fiscal year-end month)
COMPANIES = {
    "lvmh": ("LVMH MOET HENNESSY-LOUIS VUITTON", "IOG4E947OATN0KJYSD45", 12),
    "kering": ("KERING", "549300VGEJKB7SVUZR78", 12),
    "essilorluxottica": ("ESSILORLUXOTTICA", "549300M3VH1A3ER1TB49", 12),
    "danone": ("DANONE", "969500KMUQ2B6CBAF162", 12),
    "pernod_ricard": ("PERNOD RICARD", "52990097YFPX9J0H5D87", 6),
}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def esef_packages(results: list, issuer: str) -> list:
    """[(numero, document)] for the issuer's own universal registration documents that carry a zip (the ESEF package;
    filings before FY2021 are PDF only), newest first."""
    out = []
    for r in results:
        if not any(s.get("raisonSociale", "").strip().upper() == issuer.upper() for s in r.get("societes", [])):
            continue
        for d in r.get("documents", []):
            if d.get("nomFichier", "").lower().endswith(".zip"):
                out.append((r["numero"], d))
    return sorted(out, key=lambda x: x[0], reverse=True)


def candidate_year(numero: str, fye_month: int = 12) -> int:
    """The fiscal year a URD most likely covers, from the filing year in its number: a December year end is filed the
    next spring (D.26-0195 -> 2025), a June one the same autumn. Only used to skip a download that is surely not
    needed; the real period is read from the package after download."""
    filed = 2000 + int(numero.split(".")[1].split("-")[0])
    return filed - 1 if fye_month >= 10 else filed


XBRLI = "{http://www.xbrl.org/2003/instance}"
IX = "{http://www.xbrl.org/2013/inlineXBRL}"


def report_period(xhtml: bytes, lei: str) -> datetime.date:
    """From the report's own XBRL: the entity must be `lei`, and the period end is the latest end among the annual
    (300-400 day) durations that actually carry numeric facts (at least 10% of the busiest one's count). Counting facts
    matters: Pernod Ricard's FY2021 and FY2022 reports (June year end) each declare an unused context ending 31 December
    - the latest end of ANY annual context would name the wrong year. File names are not used either: ESMA's
    <LEI>-<date> naming is only a recommendation (four of five French packages checked on 2026-09-23 ignore it)."""
    import collections

    from lxml import etree
    root = etree.fromstring(xhtml, etree.XMLParser(recover=True, huge_tree=True))
    ids = {el.text.strip() for el in root.iter(XBRLI + "identifier") if el.text}
    if lei not in ids:
        raise ValueError(f"report entity {sorted(ids)[:3]} is not {lei}")
    annual = {}
    for c in root.iter(XBRLI + "context"):
        s, e = c.find(".//" + XBRLI + "startDate"), c.find(".//" + XBRLI + "endDate")
        if s is not None and e is not None:
            d0, d1 = datetime.date.fromisoformat(s.text.strip()[:10]), datetime.date.fromisoformat(e.text.strip()[:10])
            if 300 <= (d1 - d0).days + 1 <= 400:
                annual[c.get("id")] = d1
    counts = collections.Counter(annual[el.get("contextRef")] for el in root.iter(IX + "nonFraction")
                                 if el.get("contextRef") in annual)
    if not counts:
        raise ValueError("no numeric fact on an annual period in the report")
    busiest = max(counts.values())
    return max(end for end, n in counts.items() if n >= 0.1 * busiest)


def package_period(zip_path: Path, lei: str) -> datetime.date:
    """The fiscal period end of an ESEF report package (its report sits in a reports/ folder)."""
    z = zipfile.ZipFile(zip_path)
    reports = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html")) and "/reports/" in n.lower()]
    if not reports:
        raise ValueError(f"{zip_path.name}: no report in a reports/ folder - not an ESEF report package")
    return report_period(z.read(max(reports, key=lambda n: z.getinfo(n).file_size)), lei)


def intact(part: Path, announced: int, received: int) -> str:
    """'' when the file is whole, else why not: short read, or a member failing the zip's CRC check."""
    if announced and received != announced:
        return f"received {received} of {announced} bytes"
    try:
        bad = zipfile.ZipFile(part).testzip()
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as e:     # a corrupt deflate stream raises zlib.error
        return f"not a readable zip: {type(e).__name__}: {e}"
    return f"CRC error in {bad}" if bad else ""


def download(url: str, out: Path, min_free_mb: int, has_room):
    """Stream to <out>.part, check it is whole, rename. Returns the file's SHA-256, or None (keeping nothing)."""
    part = out.with_name(out.name + ".part")
    digest, received, size = hashlib.sha256(), 0, 0
    try:
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            size = int(resp.headers.get("Content-Length") or 0)
            free = shutil.disk_usage(out.parent).free
            if not has_room(free, size, min_free_mb * 1024 * 1024):
                print(f"    NOT downloaded: {size // 2**20} MB would leave {(free - size) // 2**20} MB free")
                return None
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
    except (requests.RequestException, OSError) as e:
        part.unlink(missing_ok=True)
        print(f"    FAILED, nothing kept: {type(e).__name__}: {e}")
        return None
    problem = intact(part, size, received)
    if problem:
        part.unlink(missing_ok=True)
        print(f"    REJECTED: {problem}")
        return None
    part.replace(out)
    return digest.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="+", choices=sorted(COMPANIES), help="company keys (default: all)")
    ap.add_argument("--out-dir", default="data/raw/historical")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded, fetch nothing")
    ap.add_argument("--skip-loaded", action="store_true", help="skip fiscal years whose own filing is already loaded")
    ap.add_argument("--min-year", type=int, default=2021, help="oldest fiscal year to fetch")
    ap.add_argument("--min-free-mb", type=int, default=500)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    dh = _load("download_historical", "download_historical.py")
    engine = None
    if args.skip_loaded:
        import os
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        load_dotenv()
        engine = create_engine(os.environ["DATABASE_URL"])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for key in args.only or sorted(COMPANIES):
        issuer, lei, fye_month = COMPANIES[key]
        name = dh.COMPANIES[key][1]
        print(f"\n{name} ({issuer}, {lei})")
        res = requests.get(f"{API}/informations", params={"From": 0, "Size": 50, "RechercheTexte": issuer,
                                                         "TypesDocument": "DocumentEnregistrementUniversel"}, timeout=60)
        res.raise_for_status()
        loaded = dh.loaded_period_ends(engine, name) if engine is not None else []
        for numero, doc in esef_packages(res.json()["result"], issuer):
            year = candidate_year(numero, fye_month)
            if year < args.min_year:
                continue
            if any(e.year == year for e in loaded):
                print(f"  {numero} (FY{year}): already loaded")
                continue
            url = f"{API}/documents/{doc['path']}"
            if args.dry_run:
                print(f"  {numero} (FY{year}): would download {doc['nomFichier']}")
                continue
            tmp = out_dir / f"{key}_bdif_{numero.replace('.', '')}.zip"
            sha = download(url, tmp, args.min_free_mb, dh.has_room)
            if sha is None:
                failed += 1
                continue
            try:
                period = package_period(tmp, lei)
            except (ValueError, zipfile.BadZipFile) as e:
                tmp.unlink()
                print(f"  {numero}: REJECTED - {e}")
                failed += 1
                continue
            final = out_dir / f"{key}_{period.isoformat()}.zip"
            if dh.already_loaded(period, loaded):
                tmp.unlink()
                print(f"  {numero}: period {period} is already loaded - file not kept")
                continue
            if final.exists():
                tmp.unlink()
                print(f"  {numero}: {final.name} already on disk - kept the existing file")
                continue
            tmp.replace(final)
            print(f"  {numero}: saved {final.name} ({final.stat().st_size // 1024} KB, all members CRC-checked, "
                  f"period {period} read from the package, sha256 {sha[:16]}...)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
