"""
tests/conftest.py

WHAT
----
Provides `load_script(filename)`, a pytest fixture that loads any script
under scripts/ as an importable module - needed because filenames like
`11_ratio_engine.py` can't be `import`ed normally (Python identifiers
can't start with a digit). Every script in this project that's meant to
be reused already loads its dependencies this way (see e.g.
16_forecasting.py importing 11_ratio_engine.py) - this fixture is the
same pattern, reused for tests instead of for scripts importing scripts.

WHY
---
One shared fixture instead of copy-pasting the importlib boilerplate into
every test file - if the loading mechanism ever needs to change, it
changes in one place.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _load(filename: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def load_script():
    """Usage in a test: `r11 = load_script('11_ratio_engine.py')`"""
    return _load
