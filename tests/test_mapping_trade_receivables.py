"""
tests/test_mapping_trade_receivables.py

Recordati's FY2023-FY2025 filings tag trade receivables as `ifrs-full:TradeReceivables`, which the
mapping did not know (it had `CurrentTradeReceivables` and `TradeAndOtherCurrentReceivables`), so
those facts were skipped and DSO / cash-conversion-cycle came out blank for 2023-2025.

VERIFIED AGAINST THE COMPANY'S OWN PUBLISHED STATEMENTS (the inline-XBRL annual reports inside the
ESEF packages), not against our database:
  * consolidated balance sheets FY2025 / FY2024 (English) and FY2023 (Italian, "Crediti commerciali"):
    "Trade receivables | Note 14 |" is a CURRENT-assets line, between Inventories and Other receivables,
    and there is no non-current trade receivable in any of them:
        31 Dec 2025: 570,154   2024: 516,743   2023: 445,193   2022: 361,898   (EUR thousands)
  * the FY2022 figure is also what our database already held as `CurrentTradeReceivables` from the
    FY2022 filing, so the two tags carry the same printed line across years.
  * income statements, "Net revenue": 2025 2,618,383 | 2024 2,341,559 | 2023 2,082,331 | 2022 1,853,307.
A scan of all 92 packages on disk found the tag in only 5 (Recordati x3, Ferrari, SKF) and NO package
tagging both a total and a current trade-receivables line, so mapping both to one concept cannot make one
fact overwrite the other. No database.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"


@pytest.fixture(scope="module")
def lookup(load_script):
    return load_script("09_batch_load.py").load_mapping(str(MAPPING))


class TestMapping:
    def test_trade_receivables_lands_on_the_concept_dso_reads(self, lookup):
        assert lookup["ifrs-full:TradeReceivables"][0] == "current_trade_receivables"

    def test_it_shares_the_concept_with_the_current_tag_it_replaces_in_newer_filings(self, lookup):
        assert lookup["ifrs-full:CurrentTradeReceivables"][0] == lookup["ifrs-full:TradeReceivables"][0]

    def test_the_tag_is_listed_exactly_once(self):
        text = MAPPING.read_text(encoding="utf-8")
        assert len(re.findall(r"^\s+- ifrs-full:TradeReceivables\s*$", text, flags=re.M)) == 1

    def test_the_broader_fallback_tag_is_still_a_separate_concept(self, lookup):
        """TradeAndOtherCurrentReceivables (includes non-trade) must not be merged into the specific concept."""
        assert lookup["ifrs-full:TradeAndOtherCurrentReceivables"][0] == "trade_and_other_current_receivables"


# (year, trade receivables, net revenue) exactly as printed, EUR thousands - hand-calculated DSO
RECORDATI = [
    (2022, 361_898, 1_853_307, 71.2741),
    (2023, 445_193, 2_082_331, 78.0354),
    (2024, 516_743, 2_341_559, 80.5494),
    (2025, 570_154, 2_618_383, 79.4789),
]


@pytest.mark.parametrize("year,receivables,revenue,expected_dso", RECORDATI)
def test_dso_from_the_printed_recordati_statements(load_script, year, receivables, revenue, expected_dso):
    r11 = load_script("11_ratio_engine.py")
    wide = pd.DataFrame([{"company": "Recordati", "company_id": 19, "year": year, "revenue": float(revenue),
                          "current_trade_receivables": float(receivables)}])
    assert r11.compute_ratios(wide)["dso"].iloc[0] == pytest.approx(expected_dso, abs=0.001)
