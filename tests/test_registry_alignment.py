"""
tests/test_registry_alignment.py

Phase 5 (coverage and alignment), the part that needs no database. The reviewed files name companies by their registry
name: an entry whose name drifts from data/companies.yaml ("Hermes" for "Hermes International", "Dometic" for "Dometic
Group") is silently never applied - the loader matches (company, tag) exactly, so the correction it carries just does
not happen and nothing says so. These tests make every such name resolve, and every file agree with the registry.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parent.parent
REGISTRY = REPO / "data" / "companies.yaml"
OVERRIDES = REPO / "data" / "mappings" / "company_tag_overrides.yaml"
NOTE_FIGURES = REPO / "data" / "mappings" / "reviewed_note_figures.yaml"


@pytest.fixture(scope="module")
def names():
    entries = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["companies"]
    return {e["name"] for e in entries.values()}


def test_every_override_names_a_registry_company(names):
    entries = yaml.safe_load(OVERRIDES.read_text(encoding="utf-8"))["overrides"]
    assert sorted({e["company"] for e in entries} - names) == []


def test_every_reviewed_note_figure_names_a_registry_company(names):
    figures = yaml.safe_load(NOTE_FIGURES.read_text(encoding="utf-8"))["figures"]
    assert sorted({f["company"] for f in figures} - names) == []


def test_every_registry_entry_is_loadable(names):
    """A key and an LEI make a company loadable by the archive loader and verifiable (its packages' own contexts must
    carry that LEI); a name must be unique since every reviewed file and the database match on it."""
    entries = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["companies"]
    missing = [stem for stem, e in entries.items() if not e.get("key") or not e.get("lei")]
    assert missing == []
    assert len(names) == len(entries)


def test_financial_companies_carry_their_reason():
    """reporting_model: financial blanks a company's corporate ratios and refuses its models - the reason is shown on the
    dashboard, so it must be written."""
    entries = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["companies"]
    for stem, e in entries.items():
        if e.get("reporting_model") == "financial":
            assert len(str(e.get("reporting_model_reason", "")).strip()) > 20, stem


def test_no_dashboard_wording_names_a_company(names):
    """CLAUDE.md: the dashboard's own wording stays generic, so it cannot state one company's finding for another; a
    company's finding lives in its override's dashboard_note. The leverage footnote shown for every company once quoted
    LVMH's 2024 net debt. Docstrings (never displayed) may cite the real case that motivated the code."""
    import ast
    tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docstrings.add(id(node.body[0].value))
    shown = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in docstrings]
    hits = sorted({(n.lineno, name) for n in shown for name in names if name in n.value})
    assert hits == []
