"""
tests/test_comps_ebitda_fallback.py

Regression tests for how scripts/19_valuation.py treats a company whose
filing has no D&A tag.

The real bug: for 6 of the 11 companies with comps (L'Oreal, LVMH, Kering,
Essity, EssilorLuxottica, Pernod Ricard) the ratio engine's `_ebitda` is just
EBIT (`_da_total == 0`). 21/22/24 flagged that; the comps did not, so the
dashboard showed L'Oreal "EV/EBITDA 24.3x" and "+113% premium to peers", and the
peer median mixed true EBITDA with relabelled EBIT. The comps now say "not
available" instead, leave such companies out of peer medians, and offer EV/EBIT.
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def val(load_script):
    return load_script("19_valuation.py")


class TestIsEbitdaFallback:
    @pytest.mark.parametrize("da", [0, 0.0, None, np.nan])
    def test_no_d_and_a_means_fallback(self, val, da):
        assert val.is_ebitda_fallback(da) is True

    @pytest.mark.parametrize("da", [1.0, 1.2e9, 0.5])
    def test_real_d_and_a_is_not(self, val, da):
        assert val.is_ebitda_fallback(da) is False


class TestComputeMultiples:
    def multiples(self, val, fallback, **over):
        args = dict(ev_eur=200.0, revenue_eur=40.0, ebitda_eur=10.0, ebit_eur=8.0,
                    market_cap_eur=190.0, net_income_eur=6.0, ebitda_is_fallback=fallback)
        args.update(over)
        return val.compute_multiples(**args)

    def test_normal_company_gets_every_multiple(self, val):
        m = self.multiples(val, False)
        assert m == {"ev_ebitda": 20.0, "ev_ebit": 25.0, "ev_sales": 5.0, "pe": pytest.approx(190 / 6)}

    def test_fallback_company_has_no_ev_ebitda_but_keeps_ev_ebit(self, val):
        """The L'Oreal case: 'EBITDA' is EBIT, so EV/EBITDA would just be EV/EBIT
        mislabelled - it must be None, while the honest EV/EBIT stays."""
        m = self.multiples(val, True, ebitda_eur=8.0)
        assert m["ev_ebitda"] is None and m["ev_ebit"] == 25.0

    @pytest.mark.parametrize("key,field", [("ev_ebit", "ebit_eur"), ("ev_sales", "revenue_eur"), ("pe", "net_income_eur")])
    def test_a_zero_or_negative_denominator_is_none_never_a_meaningless_multiple(self, val, key, field):
        assert self.multiples(val, False, **{field: -1.0})[key] is None
        assert self.multiples(val, False, **{field: 0.0})[key] is None


def _comps(rows):
    return pd.DataFrame(rows)


def _row(company, sector, ev_ebitda, ebitda_eur=10.0, fallback=False, market_cap=100.0, net_debt=0.0):
    return {"company": company, "sector": sector, "ev_ebitda": ev_ebitda, "ebitda_eur": ebitda_eur,
            "ebitda_is_fallback": fallback, "market_cap_eur": market_cap, "net_debt_eur": net_debt}


class TestPeerStatsIgnoreFallbackCompanies:
    def defensive(self):
        """The real Consumer Defensive shape: 3 of 5 have no D&A."""
        return _comps([
            _row("L'Oreal", "Consumer Defensive", None, ebitda_eur=None, fallback=True),
            _row("Danone", "Consumer Defensive", 8.0),
            _row("Puig", "Consumer Defensive", 12.0),
            _row("Pernod", "Consumer Defensive", None, ebitda_eur=None, fallback=True),
            _row("Essity", "Consumer Defensive", None, ebitda_eur=None, fallback=True),
        ])

    def test_the_median_is_built_only_from_real_ebitda(self, val):
        out = val.add_peer_stats(self.defensive())
        assert out["ev_ebitda_sector_median"].iloc[0] == pytest.approx(10.0)      # median(8, 12), not diluted

    def test_peer_count_is_other_companies_that_actually_contribute(self, val):
        """It used to be the sector size INCLUDING the company itself ("5 peers")."""
        out = val.add_peer_stats(self.defensive()).set_index("company")
        assert out.loc["L'Oreal", "n_peers_in_sector"] == 2       # Danone + Puig
        assert out.loc["Danone", "n_peers_in_sector"] == 1        # Puig only - not itself, not the fallbacks
        assert out.loc["Puig", "n_peers_in_sector"] == 1

    def test_a_company_with_no_sector_has_no_peer_count(self, val):
        c = _comps([_row("Solo", None, 9.0), _row("A", "X", 5.0)])
        assert pd.isna(val.add_peer_stats(c).set_index("company").loc["Solo", "n_peers_in_sector"])

    def test_a_sector_where_nobody_has_a_multiple_has_zero_peers_not_a_crash(self, val):
        c = _comps([_row("A", "X", None, fallback=True, ebitda_eur=None), _row("B", "X", None, fallback=True, ebitda_eur=None)])
        assert list(val.add_peer_stats(c)["n_peers_in_sector"]) == [0, 0]


class TestImpliedValuationNeedsTwoRealPeers:
    def test_the_fallback_company_gets_no_implied_valuation(self, val):
        """L'Oreal's '+113% premium' was computed from an EBITDA that was EBIT."""
        c = _comps([_row("L'Oreal", "S", None, ebitda_eur=None, fallback=True),
                    _row("A", "S", 8.0), _row("B", "S", 12.0)])
        out = val.add_implied_valuation(val.add_peer_stats(c)).set_index("company")
        assert pd.isna(out.loc["L'Oreal", "implied_ev_from_peers"])
        assert pd.isna(out.loc["L'Oreal", "premium_vs_peers_pct"])

    def test_one_real_peer_is_not_enough(self, val):
        """Two peers exist but one has no D&A: 'median of one' is undefined."""
        c = _comps([_row("A", "S", 10.0), _row("B", "S", 8.0),
                    _row("C", "S", None, ebitda_eur=None, fallback=True)])
        out = val.add_implied_valuation(val.add_peer_stats(c)).set_index("company")
        assert pd.isna(out.loc["A", "implied_ev_from_peers"])

    def test_two_real_peers_give_an_implied_valuation(self, val):
        c = _comps([_row("A", "S", 10.0, ebitda_eur=10.0, market_cap=100.0),
                    _row("B", "S", 8.0), _row("C", "S", 12.0)])
        out = val.add_implied_valuation(val.add_peer_stats(c)).set_index("company")
        assert out.loc["A", "implied_ev_from_peers"] == pytest.approx(100.0)      # median(8, 12) x 10
        assert out.loc["A", "premium_vs_peers_pct"] == pytest.approx(0.0)


class TestForwardMultiples:
    def history(self, company):
        return pd.DataFrame({"company": company, "year": [2021, 2022, 2023],
                             "_revenue": [100.0, 110.0, 121.0], "_ebitda": [20.0, 22.0, 24.2]})

    def comps_row(self, company, fallback):
        return pd.DataFrame([{"company": company, "ev_eur": 300.0, "quote_ccy": "EUR",
                              "ebitda_is_fallback": fallback}])

    def test_no_forward_ev_ebitda_when_ebitda_is_only_ebit(self, val):
        out = val.compute_forward_multiples(self.comps_row("L'Oreal", True), self.history("L'Oreal"), {})
        assert pd.isna(out["fwd_ev_ebitda"].iloc[0]) and pd.isna(out["fwd_ev_sales"].iloc[0])

    def test_a_normal_company_still_gets_forward_multiples(self, val):
        out = val.compute_forward_multiples(self.comps_row("Danone", False), self.history("Danone"), {})
        assert out["fwd_ev_ebitda"].iloc[0] == pytest.approx(300.0 / 26.62, rel=1e-3)


class TestDashboardFlag:
    """webapp/app.py is a Streamlit script, so its pure helper is extracted."""

    @pytest.fixture
    def flag(self):
        source = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "ebitda_is_fallback")
        ns = {"pd": pd}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
        return ns["ebitda_is_fallback"]

    def test_true_and_false(self, flag):
        assert flag(pd.Series({"ebitda_is_fallback": True})) is True
        assert flag(pd.Series({"ebitda_is_fallback": False})) is False

    @pytest.mark.parametrize("value", [None, np.nan])
    def test_missing_or_null_means_no_flag(self, flag, value):
        assert flag(pd.Series({"ebitda_is_fallback": value})) is False

    def test_a_database_without_the_column_still_renders(self, flag):
        """The comps script has not re-run since the fix: no column, no crash."""
        assert flag(pd.Series({"ev_ebitda": 12.0})) is False

    def test_the_schema_adds_the_columns_additively(self):
        sql = (REPO / "sql" / "schema_valuation.sql").read_text(encoding="utf-8")
        for col in ("ebit_eur", "ev_ebit", "ebitda_is_fallback"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql


def _row2(company, sector, ev_ebitda=None, ev_ebit=None, ebitda=None, ebit=None, fallback=False,
          market_cap=100.0, net_debt=0.0):
    return {"company": company, "sector": sector, "ev_ebitda": ev_ebitda, "ev_ebit": ev_ebit,
            "ebitda_eur": ebitda, "ebit_eur": ebit, "ebitda_is_fallback": fallback,
            "market_cap_eur": market_cap, "net_debt_eur": net_debt}


class TestEbitBasisKeepsAPeerComparisonAlive:
    """Withholding EBITDA for companies with no D&A would, on today's data, leave
    NO sector with two real EBITDA peers - the comparison would vanish for
    everyone. EV/EBIT needs only operating profit, so the same comparison is
    offered on that basis, clearly labelled."""

    def defensive(self):
        return _comps([
            _row2("L'Oreal", "S", ev_ebit=24.0, ebit=10.0, fallback=True, market_cap=200.0),
            _row2("Danone", "S", ev_ebitda=11.0, ev_ebit=15.0, ebitda=8.0, ebit=6.0),
            _row2("Puig", "S", ev_ebitda=12.0, ev_ebit=14.0, ebitda=4.0, ebit=3.0),
            _row2("Essity", "S", ev_ebit=13.0, ebit=5.0, fallback=True),
        ])

    def test_a_no_d_and_a_company_still_gets_an_ebit_based_comparison(self, val):
        out = val.add_implied_valuation(val.add_peer_stats(self.defensive())).set_index("company")
        # peers' EV/EBIT: Danone 15, Puig 14, Essity 13 -> median 14; x L'Oreal EBIT 10 = 140
        assert out.loc["L'Oreal", "implied_ev_from_peers_ebit"] == pytest.approx(140.0)
        assert out.loc["L'Oreal", "premium_vs_peers_ebit_pct"] == pytest.approx((200.0 / 140.0 - 1) * 100)

    def test_the_ebitda_comparison_is_still_withheld_for_it(self, val):
        out = val.add_implied_valuation(val.add_peer_stats(self.defensive())).set_index("company")
        assert pd.isna(out.loc["L'Oreal", "implied_ev_from_peers"])
        assert pd.isna(out.loc["L'Oreal", "premium_vs_peers_pct"])

    def test_each_basis_counts_its_own_peers(self, val):
        out = val.add_peer_stats(self.defensive()).set_index("company")
        assert out.loc["L'Oreal", "n_peers_in_sector"] == 2          # EBITDA: Danone, Puig
        assert out.loc["L'Oreal", "n_peers_ebit_in_sector"] == 3     # EBIT: Danone, Puig, Essity

    def test_sector_median_ev_ebit_is_stored(self, val):
        out = val.add_peer_stats(self.defensive())
        assert out["ev_ebit_sector_median"].iloc[0] == pytest.approx(14.5)   # median(24, 15, 14, 13)

    def test_no_ebit_means_no_comparison_never_a_guess(self, val):
        c = self.defensive()
        c.loc[c.company == "L'Oreal", ["ebit_eur", "ev_ebit"]] = None
        out = val.add_implied_valuation(val.add_peer_stats(c)).set_index("company")
        assert pd.isna(out.loc["L'Oreal", "implied_ev_from_peers_ebit"])

    def test_one_ebit_peer_is_not_enough(self, val):
        c = _comps([_row2("A", "S", ev_ebit=10.0, ebit=5.0), _row2("B", "S", ev_ebit=12.0, ebit=4.0)])
        out = val.add_implied_valuation(val.add_peer_stats(c)).set_index("company")
        assert pd.isna(out.loc["A", "implied_ev_from_peers_ebit"])

    def test_the_new_columns_are_persisted_additively(self):
        sql = (REPO / "sql" / "schema_valuation.sql").read_text(encoding="utf-8")
        for col in ("ev_ebit_sector_median", "n_peers_ebit_in_sector",
                    "implied_ev_from_peers_ebit", "premium_vs_peers_ebit_pct"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql
        source = (REPO / "scripts" / "19_valuation.py").read_text(encoding="utf-8")
        for col in ("ev_ebit_sector_median", "n_peers_ebit_in_sector",
                    "implied_ev_from_peers_ebit", "premium_vs_peers_ebit_pct", "ebitda_is_fallback"):
            assert source.count(col) >= 3, f"{col} should be in the INSERT, the UPDATE and the parameters"


class TestPeerComparisonSentence:
    """The dashboard sentence: EBITDA basis when available, EV/EBIT otherwise."""

    @pytest.fixture
    def text_for(self):
        source = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("peer_comparison_text", "format_eur")]
        ns = {"pd": pd}
        exec(compile(ast.Module(body=fns, type_ignores=[]), "app.py", "exec"), ns)
        return ns["peer_comparison_text"]

    BASE = {"sector": "Consumer Defensive",
            "n_peers_in_sector": 3, "ev_ebitda_sector_median": 11.6, "implied_ev_from_peers": 9.55e10, "premium_vs_peers_pct": 113.0,
            "n_peers_ebit_in_sector": 3, "ev_ebit_sector_median": 14.0, "implied_ev_from_peers_ebit": 1.4e11, "premium_vs_peers_ebit_pct": 45.0}

    def test_uses_the_ebitda_basis_when_it_is_available(self, text_for):
        text_ = text_for(pd.Series(self.BASE))
        assert "EV/EBITDA 11.6x" in text_ and "+113% premium" in text_ and "EBIT basis" not in text_

    def test_falls_back_to_ebit_basis_and_says_so(self, text_for):
        r = pd.Series({**self.BASE, "implied_ev_from_peers": None, "premium_vs_peers_pct": None, "n_peers_in_sector": 0})
        text_ = text_for(r)
        assert "EV/EBIT 14.0x" in text_ and "+45% premium" in text_ and "EV/EBIT basis" in text_

    def test_a_discount_reads_as_a_discount(self, text_for):
        assert "-20% discount" in text_for(pd.Series({**self.BASE, "premium_vs_peers_pct": -20.0}))

    def test_fewer_than_two_usable_peers_gives_no_sentence(self, text_for):
        r = pd.Series({**self.BASE, "n_peers_in_sector": 1, "n_peers_ebit_in_sector": 1})
        assert text_for(r) is None

    def test_a_database_without_the_ebit_columns_still_works(self, text_for):
        """The comps script has not re-run since the fix."""
        legacy = pd.Series({k: v for k, v in self.BASE.items() if "ebit_" not in k and k != "n_peers_ebit_in_sector"})
        assert "EV/EBITDA" in text_for(legacy)
        assert text_for(pd.Series({**legacy, "implied_ev_from_peers": None})) is None
