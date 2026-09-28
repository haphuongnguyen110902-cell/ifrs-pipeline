"""
tests/test_phase4_printed_lines.py

Phase 4 added 35 companies. Reading every new balance sheet row by row (the printed label beside each tagged value, not
the tag) found lines the ratio engine could not see or read wrongly:

  * generic IFRS elements it did not list: the current portion of non-current borrowings on a line of its own (Eni
    3,434M beside 4,929M of short-term debt, AB InBev 885M, Dometic 2,388M), bank overdrafts (AB InBev), "loans
    received" (Webuild), one unclassified "Debt" line (Ferrari, IAS 1.60), trade payables / receivables and "trade and
    other payables" tagged without "current" (SKF, Ferrari, Carrefour), and the operating-profit extension Carrefour and
    Safran share with LVMH;
  * the IFRS "other financial liabilities" element is a residual whose meaning depends on the filer: Schneider, Arjo,
    Essity, Moncler and Amplifon print their BORROWINGS under it and Recordati its short-term bank debt, while Safran
    (payables for fixed-asset purchases), Cofinimmo (derivatives), Dometic and JM ("other liabilities") do not. The tag
    cannot tell which, so the engine keeps counting it and those four are corrected by reviewed per-company entries;
  * extension elements for the borrowings themselves (Safran, RELX, Umicore, JM, Peab, Cofinimmo) and working capital
    (Thales, RELX), read through reviewed entries in company_tag_overrides.yaml.

Before: Safran's net debt was 23 + 174 of "other financial liabilities" less 6.8bn of cash, while its own net financial
position counts 5,051M of interest-bearing liabilities; SKF's left out its 12,685M of bonds and loans. Golden values are
the companies' printed statements. No database.
"""
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).parent.parent
OVERRIDES = REPO / "data" / "mappings" / "company_tag_overrides.yaml"
MAPPING = REPO / "data" / "mappings" / "ifrs_concepts_v0.yaml"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide(**cols):
    return pd.DataFrame([{"company": "X", "company_id": 1, "year": 2025, **cols}])


def debt(r11, **cols):
    return r11.compute_financial_debt(wide(**cols)).iloc[0]


class TestGenericDebtLines:
    def test_eni_2025_current_portion_beside_short_term_debt(self, r11):
        """Printed (EUR millions): long-term financial liabilities 20,139, long-term lease liabilities 4,437,
        short-term financial liabilities 4,929, current portion of long-term financial liabilities 3,434, current
        portion of lease liabilities 1,263."""
        d = debt(r11, longterm_borrowings=20_139.0, noncurrent_lease_liabilities=4_437.0, shortterm_borrowings=4_929.0,
                 current_portion_of_longterm_borrowings=3_434.0, current_lease_liabilities=1_263.0)
        assert d["financial_debt"] == pytest.approx(34_202.0) and d["complete"]

    def test_ab_inbev_2025_current_portion_and_overdrafts_are_the_current_side(self, r11):
        """Printed (USD millions): interest-bearing loans and borrowings 72,128 non-current, 885 current; bank
        overdrafts 14. Without the current portion the current side was empty and net debt blank."""
        d = debt(r11, longterm_borrowings=72_128.0, current_portion_of_longterm_borrowings=885.0,
                 current_bank_overdrafts=14.0)
        assert d["financial_debt"] == pytest.approx(73_027.0) and d["complete"]

    def test_a_child_beside_its_parent_is_not_counted_twice(self, r11):
        d = debt(r11, longterm_borrowings=100.0, current_borrowings_and_current_portion_of_noncurrent_borr_etc=50.0,
                 current_portion_of_longterm_borrowings=30.0, current_bank_overdrafts=5.0)
        assert d["financial_debt"] == 150.0
        d = debt(r11, longterm_borrowings=100.0, shortterm_borrowings=50.0, current_bank_overdrafts=5.0)
        assert d["financial_debt"] == 150.0

    def test_webuild_loans_received_are_borrowings(self, r11):
        d = debt(r11, noncurrent_portion_of_noncurrent_loans_received=137.8, noncurrent_portion_of_noncurrent_bonds_issued=1_892.2,
                 current_borrowings_and_current_portion_of_noncurrent_borr_etc=490.3)
        assert d["financial_debt"] == pytest.approx(2_520.3) and "noncurrent_portion_of_noncurrent_loans_received" in d["basis"]

    def test_ferrari_one_unclassified_debt_line_is_complete(self, r11):
        """IAS 1.60 liquidity presentation: Ferrari prints one "Debt" line, 2,884,220 (EUR thousands), no split."""
        d = debt(r11, borrowings=2_884_220.0)
        assert d["financial_debt"] == 2_884_220.0 and d["basis"] == "borrowings" and d["complete"]

    def test_the_unclassified_total_is_ignored_beside_split_lines(self, r11):
        """It would contain them: a split line wins, and a lone lease line cannot be completed by it either."""
        d = debt(r11, borrowings=1_000.0, longterm_borrowings=600.0, shortterm_borrowings=400.0)
        assert d["financial_debt"] == 1_000.0 and "borrowings" not in d["basis"].split("+")
        d = debt(r11, borrowings=1_000.0, noncurrent_lease_liabilities=50.0)
        assert d["financial_debt"] == 50.0 and not d["complete"]

    def test_the_not_borrowings_concepts_are_not_debt(self, r11):
        d = debt(r11, longterm_borrowings=2_446.0, shortterm_borrowings=2_605.0,
                 other_noncurrent_financial_liabilities_not_borrowings=23.0,
                 other_current_financial_liabilities_not_borrowings=174.0)
        assert d["financial_debt"] == pytest.approx(5_051.0)


class TestWorkingCapitalAndEbitVariants:
    def test_payables_tagged_without_current(self, r11):
        """SKF "Leverantörsskulder" 12,553 and Ferrari "Trade payables" 841.3 carry the element without "current"."""
        out = r11.compute_ratios(wide(revenue=100_000.0, cost_of_sales=80_000.0,
                                      trade_and_other_payables_to_trade_suppliers=12_553.0))
        assert out["dpo"].iloc[0] == pytest.approx(12_553.0 / 80_000.0 * 365)
        assert out["_payables_basis"].iloc[0] == "trade_and_other_payables_to_trade_suppliers"

    def test_carrefour_trade_and_other_payables_is_the_broader_basis(self, r11):
        out = r11.compute_ratios(wide(revenue=90_000.0, cost_of_sales=70_000.0, trade_and_other_payables=14_690.0,
                                      trade_and_other_receivables=3_193.0))
        assert out["_payables_basis"].iloc[0] == "trade_and_other_payables"
        assert "trade_and_other_payables" in r11.BROADER_PAYABLES
        assert "trade_and_other_payables_to_trade_suppliers" not in r11.BROADER_PAYABLES
        assert out["dso"].iloc[0] == pytest.approx(3_193.0 / 90_000.0 * 365)

    def test_the_current_trade_lines_still_win(self, r11):
        out = r11.compute_ratios(wide(revenue=1_000.0, cost_of_sales=500.0,
                                      trade_and_other_current_payables_to_trade_suppliers=50.0,
                                      trade_and_other_payables_to_trade_suppliers=70.0, trade_and_other_payables=90.0,
                                      current_trade_receivables=100.0, trade_and_other_receivables=150.0))
        assert out["_payables_basis"].iloc[0] == "trade_and_other_current_payables_to_trade_suppliers"
        assert out["dso"].iloc[0] == pytest.approx(100.0 / 1_000.0 * 365)

    def test_carrefour_operating_profit_is_resultat_operationnel(self, r11):
        """Carrefour 2025: "Résultat opérationnel" 2,137 (after non-recurring items), not the recurring 2,158."""
        out = r11.compute_ratios(wide(revenue=90_000.0,
                                      profit_loss_from_operating_activities_after_share_of_prof_etc_v2=2_137.0))
        assert out["_ebit"].iloc[0] == 2_137.0 and out["_ebit_basis"].iloc[0] == ""

    def test_lvmh_keeps_its_own_line(self, r11):
        out = r11.compute_ratios(wide(revenue=80_000.0,
                                      profit_loss_from_operating_activities_after_share_of_prof_etc=17_500.0,
                                      profit_loss_from_operating_activities_after_share_of_prof_etc_v2=17_099.0))
        assert out["_ebit"].iloc[0] == 17_500.0

    def test_both_tags_map_to_the_shared_concept(self, load_script):
        lookup = load_script("09_batch_load.py").load_mapping(str(MAPPING))
        for tag in ("carrefour:ProfitLossFromOperatingActivitiesAfterShareOfProfitLossOfAssociatesAndJointVenturesInOperatingActivity",
                    "safran:ProfitLossFromOperatingActivitiesAfterShareOfProfitLossOfAssociatesAndJointVenturesInOperatingActivity"):
            assert lookup[tag][0] == "profit_loss_from_operating_activities_after_share_of_prof_etc_v2"


class TestRatiosOverANonPositiveOperatingProfit:
    """Dometic 2024 (goodwill impairment): operating profit negative, net debt SEK 13.4bn positive - the engine printed
    -11.9x of "leverage". A multiple over a zero or negative denominator is not meaningful: blank, reason stored."""

    def test_dometic_2024_is_blank_with_its_reason(self, r11):
        out = r11.compute_ratios(wide(revenue=26_000.0, profit_loss_from_operating_activities=-1_186.0,
                                      cash_flows_from_used_in_operating_activities=3_000.0,
                                      longterm_borrowings=13_077.0, current_portion_of_longterm_borrowings=2_388.0,
                                      cash_and_cash_equivalents=2_054.0))
        assert out["_net_debt"].iloc[0] == pytest.approx(13_411.0)
        assert pd.isna(out["net_debt_ebitda_proxy"].iloc[0]) and pd.isna(out["cash_conversion"].iloc[0])
        notes = r11.ebit_not_positive_notes(out)
        assert set(notes) == {(1, 2025, "cash_conversion"), (1, 2025, "net_debt_ebitda_proxy")}
        assert "zero or negative" in notes[(1, 2025, "cash_conversion")]

    def test_net_cash_over_a_positive_profit_keeps_its_negative_multiple(self, r11):
        """Safran 2025: net financial position +1,738 (net cash) over operating profit 4,308 - a real -0.40x."""
        out = r11.compute_ratios(wide(revenue=31_000.0, profit_loss_from_operating_activities=4_308.0,
                                      longterm_borrowings=2_446.0, shortterm_borrowings=2_605.0,
                                      cash_and_cash_equivalents=6_789.0))
        assert out["net_debt_ebitda_proxy"].iloc[0] == pytest.approx(-1_738.0 / 4_308.0)
        assert r11.ebit_not_positive_notes(out) == {}

    def test_the_flag_comes_from_the_same_basis_as_the_ratios(self, r11):
        assert "_ebit_not_positive" in r11.AS_REPORTED_COLUMNS


class TestReviewedEntries:
    @pytest.fixture(scope="class")
    def entries(self):
        return {(e["company"], e["tag"]): e for e in yaml.safe_load(OVERRIDES.read_text(encoding="utf-8"))["overrides"]}

    @pytest.mark.parametrize("company,tag,concept", [
        ("Safran", "safran:NonCurrentInterestBearingFinancialLiablities", "longterm_borrowings"),
        ("Safran", "safran:CurrentInterestBearingFinancialLiabilities", "shortterm_borrowings"),
        ("Safran", "ifrs-full:OtherNoncurrentFinancialLiabilities", "other_noncurrent_financial_liabilities_not_borrowings"),
        ("Safran", "ifrs-full:OtherCurrentFinancialLiabilities", "other_current_financial_liabilities_not_borrowings"),
        ("Cofinimmo", "cofb:LongTermBankBorrowings", "longterm_borrowings"),
        ("Cofinimmo", "cofb:CurrentBankBorrowingsAndCurrentPortionOfNonCurrentBankBorrowings", "shortterm_borrowings"),
        ("Cofinimmo", "ifrs-full:OtherNoncurrentFinancialLiabilities", "other_noncurrent_financial_liabilities_not_borrowings"),
        ("Dometic Group", "ifrs-full:OtherCurrentFinancialLiabilities", "other_current_liabilities"),
        ("JM", "ifrs-full:OtherCurrentFinancialLiabilities", "other_current_liabilities"),
        ("SKF", "ifrs-full:BondsIssued", "noncurrent_financial_liabilities"),
        ("RELX", "rel:NonCurrentBorrowings", "longterm_borrowings"),
        ("Thales", "tha:FournisseursEtAutresDettesCourantes", "trade_and_other_current_payables"),
    ])
    def test_the_line_is_read_as_what_the_company_prints(self, entries, company, tag, concept):
        assert entries[(company, tag)]["concept"] == concept

    def test_safran_net_financial_position_is_the_two_interest_bearing_lines(self):
        """Note 6.5: interest-bearing financial liabilities (B) 4,776 (2024) and 5,051 (2025)."""
        assert 3_788 + 988 == 4_776 and 2_446 + 2_605 == 5_051

    def test_the_evidence_sums_add_up(self):
        assert 1_101 + 4_557 + 3_429 + 3_307 + 200 + 91 == 12_685             # SKF note 20
        assert abs(511_296 + 0 + 1_077_239 - 1_588_536) <= 1                   # Cofinimmo, EUR thousands (rounding)
        assert 159_712 + 0 + 839_984 == 999_696

    def test_the_other_financial_liabilities_element_stays_debt_for_everyone_else(self, r11, entries):
        """Recordati prints its short-term bank debt under it; a generic "residual" rule would have dropped it."""
        assert "other_current_financial_liabilities" in r11.CURRENT_DEBT_LINES
        assert "other_noncurrent_financial_liabilities" in r11.NONCURRENT_DEBT_LINES
        assert not any(c == "Recordati" for c, _ in entries)

    def test_the_debt_numbers_follow_through_the_entries(self, r11, load_script):
        """The loader's own reading of Safran's lines, fed to the engine: 5,051M of borrowings, the 197M left out."""
        m09 = load_script("09_batch_load.py")
        lookup = m09.tag_lookup_for("Safran", m09.load_mapping(str(MAPPING)), m09.load_overrides())
        printed = {"safran:NonCurrentInterestBearingFinancialLiablities": 2_446.0,
                   "safran:CurrentInterestBearingFinancialLiabilities": 2_605.0,
                   "ifrs-full:OtherNoncurrentFinancialLiabilities": 23.0,
                   "ifrs-full:OtherCurrentFinancialLiabilities": 174.0}
        d = debt(r11, **{lookup[t][0]: v for t, v in printed.items()})
        assert d["financial_debt"] == pytest.approx(5_051.0) and d["complete"]
