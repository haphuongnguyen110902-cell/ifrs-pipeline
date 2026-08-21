"""
Week 1 starter script.

Goal: load one ESEF/XBRL filing with Arelle's Python API and dump every
fact into a pandas DataFrame, unfiltered. Don't try to be clean yet -
the point of this script is to SEE what raw data actually looks like
before you design the mapping layer in week 3.

Usage:
    python scripts/01_explore_filing.py data/raw/<company>.zip
"""
import sys
import zipfile
from pathlib import Path

import pandas as pd
from arelle import Cntlr, PackageManager


def load_filing(filepath: str, package_zip: str = None):
    """Load an XBRL/ESEF filing (zip package or single .xhtml) via Arelle.

    logFileName="logToPrint" makes Arelle print every warning/error it
    encounters to the console instead of hiding them - this is the key
    change that surfaces WHY a filing loaded with 0 facts.

    package_zip: if given, registers this zip as a "taxonomy package" first.
    This reads META-INF/catalog.xml inside the zip, which tells Arelle
    "when you see namespace X, the real file is at this path INSIDE the
    zip, not on the live internet." Without this step, Arelle tries to
    fetch schema files from the company's namespace URL over the web,
    which usually fails (those URLs are identifiers, not real download
    locations) and silently kills every fact.
    """
    controller = Cntlr.Cntlr(logFileName="logToPrint")
    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)
    model_xbrl = controller.modelManager.load(filepath)
    if model_xbrl is None:
        raise RuntimeError(f"Arelle failed to load: {filepath}")
    return controller, model_xbrl


def inspect_zip_entry_point(filepath: str):
    """Print what's actually inside the zip, to help pick the right entry
    point if Arelle guessed wrong. ESEF packages typically have the real
    report .xhtml nested under META-INF/ or a reports/ subfolder."""
    print(f"\n--- Contents of {filepath} ---")
    try:
        with zipfile.ZipFile(filepath) as z:
            names = z.namelist()
            for n in names:
                print(" ", n)
            xhtml_candidates = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
            print(f"\nLikely report entry point(s): {xhtml_candidates}")
            return xhtml_candidates
    except zipfile.BadZipFile:
        print("  Not a valid zip file - the download may be corrupted or incomplete.")
        return []


def dump_facts(model_xbrl) -> pd.DataFrame:
    """Pull every fact into a flat DataFrame: concept, value, context, unit."""
    rows = []
    for fact in model_xbrl.facts:
        if fact.concept is None or not fact.concept.isNumeric:
            continue  # skip text blocks / non-numeric facts for this first pass
        context = fact.context
        rows.append({
            "concept_qname": str(fact.qname),
            "value": fact.value,
            "context_id": context.id if context is not None else None,
            "period_start": getattr(context, "startDatetime", None),
            "period_end": getattr(context, "endDatetime", None) or getattr(context, "instantDatetime", None),
            "unit": str(fact.unit.value) if fact.unit is not None else None,
            "decimals": fact.decimals,
            "dimensions": [str(d) for d in context.qnameDims.keys()] if context is not None and context.qnameDims else [],
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/01_explore_filing.py <path_to_filing>")
        sys.exit(1)

    filepath = sys.argv[1]
    if not Path(filepath).exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    controller, model_xbrl = load_filing(filepath, package_zip=filepath)
    df = dump_facts(model_xbrl)

    if len(df) == 0:
        print("\n*** Loaded 0 facts - Arelle likely picked the wrong entry point inside the zip. ***")
        controller.close()
        candidates = inspect_zip_entry_point(filepath)
        if candidates:
            entry = candidates[0]
            print(f"\nRetrying by loading the specific entry point: {entry}")
            retry_path = f"{filepath}/{entry}"  # Arelle understands zip/inner-path syntax
            controller, model_xbrl = load_filing(retry_path, package_zip=filepath)
            df = dump_facts(model_xbrl)
            print(f"Loaded {len(df)} numeric facts on retry")
        else:
            print("Could not find an .xhtml file inside the zip to retry with.")
            sys.exit(1)
    else:
        print(f"Loaded {len(df)} numeric facts")

    print(df.head(20))

    out_path = Path("data/raw") / (Path(filepath).stem + "_facts.csv")
    df.to_csv(out_path, index=False)
    print(f"Saved full dump to {out_path}")

    controller.close()
