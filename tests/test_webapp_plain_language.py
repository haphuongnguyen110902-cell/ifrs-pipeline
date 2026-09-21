"""
tests/test_webapp_plain_language.py

The public dashboard is read by people who have never seen this repository. The
full review found five empty-state messages that told a visitor to "see
22_dcf.py" / "23_market_risk.py" / "CLAUDE.md" (verified by rendering the real
app for all 16 companies: each of the 5 newest companies showed six of them).
A visitor cannot act on a script filename; it also reads as unfinished.

This scans every string passed to a `st.<text function>` call in webapp/app.py
(multi-line and f-string parts included - a line-based grep misses those) and
fails on developer references. It needs no database, so it runs in CI.
"""
import ast
import re
from pathlib import Path

APP = Path(__file__).parent.parent / "webapp" / "app.py"

TEXT_FUNCTIONS = {"info", "warning", "error", "caption", "success", "markdown",
                  "subheader", "header", "title", "write", "text", "metric"}
DEVELOPER_REFERENCE = re.compile(r"\b[\w-]+\.py\b|CLAUDE\.md|PLAN\.md|ROADMAP|SCOPE\.md|LEARNING\.md|\bWP\d")


def ui_strings(source: str):
    """(line, text) for every string literal inside a st.<text function>(...) call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in TEXT_FUNCTIONS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "st"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.append((sub.lineno, sub.value))
    return found


def offenders(source: str):
    return [(ln, m.group(0)) for ln, text in ui_strings(source)
            for m in [DEVELOPER_REFERENCE.search(text)] if m]


def test_no_visitor_facing_text_points_at_a_script_or_a_repo_document():
    bad = offenders(APP.read_text(encoding="utf-8"))
    assert not bad, (
        "developer references in text shown to dashboard visitors (line, match): "
        f"{bad} - say what is missing in plain words instead")


class TestTheCheckerItself:
    """A guard that cannot fail proves nothing: the scanner must see what a grep would miss."""

    def test_it_sees_a_reference_split_across_lines(self):
        src = 'import streamlit as st\nst.info("No DCF yet "\n        "(see 22_dcf.py) - sorry")\n'
        assert offenders(src) == [(2, "22_dcf.py")]

    def test_it_sees_a_reference_inside_an_f_string(self):
        src = 'import streamlit as st\nx = 1\nst.caption(f"Base year {x} - see 22_dcf.py")\n'
        assert offenders(src)

    def test_it_ignores_code_comments_and_non_ui_strings(self):
        src = ('import streamlit as st\n# see 22_dcf.py\nPATH = "scripts/22_dcf.py"\n'
               'def f():\n    """Loaded by 11_ratio_engine.py"""\nst.info("Nothing to see")\n')
        assert offenders(src) == []

    def test_it_flags_repo_documents_and_work_package_ids(self):
        for text in ("see CLAUDE.md", "per PLAN.md", "WP5 landing page"):
            assert offenders(f'import streamlit as st\nst.info("{text}")\n'), text
