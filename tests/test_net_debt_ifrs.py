"""
tests/test_net_debt_ifrs.py

Net debt used to be: LongtermBorrowings (else TOTAL non-current liabilities) + one current-borrowings concept
- cash. Checked against the companies' own balance sheets, that was wrong in three ways:
  * most filers do not use those two tags (8 of 16 companies took the fallback in most years);
  * the fallback includes provisions, deferred tax and pensions, and misses all current debt
    (Recordati 2025: about -8%; Danone 2024: about -4%, only because two mistakes offset);
  * IFRS 16 lease liabilities were ignored altogether (LVMH: EUR 17.8bn) although EBIT is post-IFRS 16.

Financial debt is now the sum of the financial-liability lines the company PRINTS (IAS 1.54(m): borrowings,
bonds, lease liabilities, other financial liabilities; not derivatives, trade payables, provisions) and is
BLANK where no such line is stored - never a guess.

Golden values are the companies' printed balance sheets (inline-XBRL annual reports). No database.
"""
from pathlib import Path

import pandas as pd
import pytest

MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide(**cols):
    return pd.DataFrame([{"company": "X", "company_id": 1, "year": 2025, **cols}])


def net_debt(r11, **cols):
    return r11.compute_ratios(wide(**cols))["_net_debt"].iloc[0]


class TestPrintedBalanceSheets:
    def test_recordati_2025(self, r11):
        """Printed (EUR thousands): loans due after one year 2,130,296; loans due within one year 313,341;
        short-term debts to banks and other lenders 23,849; cash and cash equivalents 428,824."""
        nd = net_debt(r11, longterm_borrowings=2_130_296e3, shortterm_borrowings=313_341e3,
                      other_current_financial_liabilities=23_849e3, cash_and_cash_equivalents=428_824e3,
                      noncurrent_liabilities=2_279_821e3)           # the old fallback base: must be ignored
        assert nd == pytest.approx(2_038_662e3)

    def test_danone_2024(self, r11):
        """Printed (EUR millions): non-current financial liabilities 10,175; short-term borrowings 4,627; cash 1,475."""
        nd = net_debt(r11, noncurrent_financial_liabilities=10_175e6, shortterm_borrowings=4_627e6,
                      cash_and_cash_equivalents=1_475e6, noncurrent_liabilities=13_707e6)
        assert nd == pytest.approx(13_327e6)

    def test_amplifon_2025_lease_lines_are_separate_printed_lines(self, r11):
        d = r11.compute_financial_debt(wide(noncurrent_financial_liabilities=984e6, other_current_financial_liabilities=359e6,
                                            noncurrent_lease_liabilities=364e6, current_lease_liabilities=122e6))
        assert d["financial_debt"].iloc[0] == pytest.approx(1_829e6)

    def test_kering_a_subtotal_beside_real_borrowings_is_left_out_never_double_counted(self, r11):
        """Whether 13M of 'non-current financial liabilities' sits beside 10,026M of borrowings or contains them
        cannot be told from the numbers; the safe rule leaves it out (understates by 13M, never doubles)."""
        d = r11.compute_financial_debt(wide(noncurrent_financial_liabilities=13e6, longterm_borrowings=10_026e6,
                                            noncurrent_lease_liabilities=4_511e6))
        assert d["financial_debt"].iloc[0] == pytest.approx(14_537e6)
        assert "noncurrent_financial_liabilities" not in d["basis"].iloc[0]


class TestRules:
    def test_no_debt_line_means_blank_never_the_old_total_liabilities_fallback(self, r11):
        d = r11.compute_financial_debt(wide(noncurrent_liabilities=5_000.0, cash_and_cash_equivalents=100.0))
        assert pd.isna(d["financial_debt"].iloc[0]) and d["basis"].iloc[0] == ""
        assert pd.isna(net_debt(r11, noncurrent_liabilities=5_000.0, cash_and_cash_equivalents=100.0))

    def test_lease_liabilities_are_included(self, r11):
        assert net_debt(r11, longterm_borrowings=100.0, noncurrent_lease_liabilities=50.0, shortterm_borrowings=10.0,
                        cash_and_cash_equivalents=20.0) == pytest.approx(140.0)

    def test_only_a_current_line_stored_is_blank_not_a_fake_net_cash_position(self, r11):
        """Recordati before its loans were loaded: a 23.8M current line + 428.8M cash looked like -405M of net cash,
        while the printed loans make it about EUR 2.0bn of net debt."""
        w = wide(other_current_financial_liabilities=23_849e3, cash_and_cash_equivalents=428_824e3)
        assert pd.isna(net_debt(r11, other_current_financial_liabilities=23_849e3, cash_and_cash_equivalents=428_824e3))
        d = r11.compute_financial_debt(w)
        assert not d["complete"].iloc[0] and d["financial_debt"].iloc[0] == 23_849e3
        assert r11.compute_ratios(w)["_debt_basis"].iloc[0].startswith("incomplete: ")

    def test_only_a_non_current_line_stored_is_blank_too(self, r11):
        assert pd.isna(net_debt(r11, longterm_borrowings=100.0, cash_and_cash_equivalents=20.0))

    def test_both_sides_stored_gives_a_number_and_a_clean_basis(self, r11):
        w = wide(longterm_borrowings=100.0, shortterm_borrowings=10.0, cash_and_cash_equivalents=20.0)
        assert r11.compute_ratios(w)["_net_debt"].iloc[0] == 90.0
        assert r11.compute_ratios(w)["_debt_basis"].iloc[0] == "longterm_borrowings+shortterm_borrowings"

    def test_derivatives_trade_payables_and_provisions_are_not_debt(self, r11):
        d = r11.compute_financial_debt(wide(longterm_borrowings=100.0, noncurrent_derivative_financial_liabilities=40.0,
                                            trade_and_other_current_payables=900.0, noncurrent_liabilities=1_000.0))
        assert d["financial_debt"].iloc[0] == 100.0

    def test_a_financial_liabilities_total_is_never_added_on_top_of_borrowing_lines(self, r11):
        d = r11.compute_financial_debt(wide(noncurrent_financial_liabilities=1_000.0, longterm_borrowings=400.0,
                                            noncurrent_lease_liabilities=600.0))
        assert d["financial_debt"].iloc[0] == 1_000.0          # 400 + 600, the 1,000 total left out

    def test_a_sibling_subtotal_larger_than_a_lease_line_is_kept(self, r11):
        """Amplifon: 984M of financial liabilities beside 364M of leases - the earlier magnitude heuristic dropped it."""
        d = r11.compute_financial_debt(wide(noncurrent_financial_liabilities=984.0, noncurrent_lease_liabilities=364.0))
        assert d["financial_debt"].iloc[0] == 1_348.0

    def test_the_total_stands_alone_when_there_is_no_detail(self, r11):
        assert r11.compute_financial_debt(wide(current_financial_liabilities=588.0))["financial_debt"].iloc[0] == 588.0

    def test_current_borrowings_incl_current_portion_contains_short_term_borrowings(self, r11):
        d = r11.compute_financial_debt(wide(current_borrowings_and_current_portion_of_noncurrent_borr_etc=500.0, shortterm_borrowings=300.0))
        assert d["financial_debt"].iloc[0] == 500.0
        d = r11.compute_financial_debt(wide(current_borrowings_and_current_portion_of_noncurrent_borr_etc=200.0, shortterm_borrowings=300.0))
        assert d["financial_debt"].iloc[0] == 500.0           # a smaller "parent" cannot contain the child

    def test_signs_as_stored_do_not_matter(self, r11):
        assert r11.compute_financial_debt(wide(longterm_borrowings=-100.0))["financial_debt"].iloc[0] == 100.0

    def test_the_basis_names_every_line_that_was_summed(self, r11):
        d = r11.compute_financial_debt(wide(longterm_borrowings=1.0, shortterm_borrowings=2.0, current_lease_liabilities=3.0))
        assert d["basis"].iloc[0] == "current_lease_liabilities+longterm_borrowings+shortterm_borrowings"

    def test_rows_are_independent(self, r11):
        w = pd.DataFrame([{"company": "A", "company_id": 1, "year": 2024, "longterm_borrowings": 10.0},
                          {"company": "B", "company_id": 2, "year": 2024, "noncurrent_liabilities": 99.0},
                          {"company": "C", "company_id": 3, "year": 2024, "shortterm_borrowings": 5.0, "current_lease_liabilities": 1.0}])
        d = r11.compute_financial_debt(w)["financial_debt"].tolist()
        assert d[0] == 10.0 and pd.isna(d[1]) and d[2] == 6.0

    def test_leverage_and_invested_capital_use_the_new_net_debt(self, r11):
        w = wide(longterm_borrowings=200.0, noncurrent_lease_liabilities=100.0, shortterm_borrowings=50.0,
                 cash_and_cash_equivalents=50.0, profit_loss_from_operating_activities=125.0,
                 equity_attributable_to_owners_of_parent=500.0)
        out = r11.compute_ratios(w)
        assert out["_net_debt"].iloc[0] == 300.0 and out["net_debt_ebitda_proxy"].iloc[0] == pytest.approx(2.4)


class TestRecordatiMapping:
    """Recordati anchors its loan lines itself (definition linkbase, identical in FY2021-FY2025):
    Rec:LoansDueAfterOneYear -> LongtermBorrowings + NoncurrentLeaseLiabilities,
    Rec:LoansDueWithinOneYear -> ShorttermBorrowings + CurrentLeaseLiabilities - i.e. the company itself declares
    that these lines include lease liabilities, so no separate lease line is added for it."""

    @pytest.fixture(scope="class")
    def lookup(self, load_script):
        return load_script("09_batch_load.py").load_mapping(str(MAPPING))

    @pytest.mark.parametrize("tag,concept", [
        ("Rec:LoansDueAfterOneYear", "longterm_borrowings"),
        ("Rec:LongtermBorrowingsAndNoncurrentLeaseLiabilities", "longterm_borrowings"),
        ("Rec:LoansDueWithinOneYear", "shortterm_borrowings"),
        ("Rec:ShorttermBorrowingsAndCurrentLeaseLiabilities", "shortterm_borrowings"),
        # the Italian-language editions (FY2022, FY2023) carry the same anchors under Italian tag names; an earlier
        # classification pass had given each its own one-off concept the engine could not see
        ("Rec:FinanziamentiDovutiOltreUnAnno", "longterm_borrowings"),
        ("Rec:FinanziamentiDovutiEntroUnAnno", "shortterm_borrowings"),
    ])
    def test_tags_follow_the_companys_own_anchors(self, lookup, tag, concept):
        assert lookup[tag][0] == concept
