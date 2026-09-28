"""
tests/test_dashboard_db_driver.py

The public dashboard was down from about 2026-09-26 to 2026-09-28 (the "Keep Dashboard Awake" job failed nine times
in a row): Streamlit Cloud rebuilt it, webapp/requirements.txt allowed SQLAlchemy < 3, so 2.1 was installed - and
SQLAlchemy 2.1 made a plain postgresql:// URL mean the psycopg 3 driver, which is not installed ("ModuleNotFoundError:
psycopg"). The app now names its driver in the URL, and the requirements keep SQLAlchemy below 2.1 until 2.1 is
tested. No database.
"""
import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def sqlalchemy_url():
    tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
    parts = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "sqlalchemy_url"]
    assert len(parts) == 1
    ns = {}
    exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
    return ns["sqlalchemy_url"]


class TestDriverIsNamed:
    @pytest.mark.parametrize("given", ["postgresql://u:p@host/db?sslmode=require", "postgres://u:p@host/db"])
    def test_a_plain_url_gets_the_installed_driver(self, sqlalchemy_url, given):
        assert sqlalchemy_url(given) == "postgresql+psycopg2://u:p@host/db" + given.split("/db", 1)[1]

    def test_a_url_that_names_its_driver_is_left_alone(self, sqlalchemy_url):
        assert sqlalchemy_url("postgresql+psycopg2://u@h/d") == "postgresql+psycopg2://u@h/d"


class TestRequirements:
    def test_the_driver_the_url_names_is_installed_and_sqlalchemy_is_below_2_1(self):
        text = (REPO / "webapp" / "requirements.txt").read_text(encoding="utf-8")
        specs = {m.group(1).lower(): m.group(2) for m in re.finditer(r"^([A-Za-z0-9_.-]+)\s*([<>=!,.\d\s]*)$", text, re.M)}
        assert "psycopg2-binary" in specs
        assert "<2.1" in specs["sqlalchemy"].replace(" ", "")
