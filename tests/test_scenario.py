"""
tests/test_scenario.py

Regression tests for scripts/25_scenario.py. No test touches the
database, market data, or the DCF/3-statement chain's live-fetching
paths - apply_shocks() and format_delta_pct() are pure functions,
tested directly, same "synthetic data, no live calls" convention as
every other test file in this project.
"""
import pytest


@pytest.fixture(scope="module")
def scenario(load_script):
    return load_script("25_scenario.py")


class TestApplyShocks:
    def make_base(self, **overrides):
        row = {"revenue": 1000.0, "operating_margin": 20.0, "capex": 50.0,
               "tax_rate": 25.0, "base_year": 2025}
        row.update(overrides)
        return row

    def test_revenue_shock_is_multiplicative(self, scenario):
        base = self.make_base()
        shocked = scenario.apply_shocks(base, revenue_shock=-0.10)
        assert shocked["revenue"] == pytest.approx(900.0)

    def test_margin_shock_is_additive_in_percentage_points(self, scenario):
        base = self.make_base()
        shocked = scenario.apply_shocks(base, margin_shock=-2.0)
        assert shocked["operating_margin"] == pytest.approx(18.0)

    def test_capex_shock_is_multiplicative(self, scenario):
        base = self.make_base()
        shocked = scenario.apply_shocks(base, capex_shock=-0.10)
        assert shocked["capex"] == pytest.approx(45.0)

    def test_no_shocks_returns_unchanged_values(self, scenario):
        base = self.make_base()
        shocked = scenario.apply_shocks(base)
        assert shocked["revenue"] == base["revenue"]
        assert shocked["operating_margin"] == base["operating_margin"]
        assert shocked["capex"] == base["capex"]

    def test_does_not_mutate_the_input(self, scenario):
        """apply_shocks() must return a NEW dict - mutating the caller's
        base dict in place would silently corrupt the "base" scenario's
        own inputs when run_scenario() is called with it afterward."""
        base = self.make_base()
        original_revenue = base["revenue"]
        scenario.apply_shocks(base, revenue_shock=-0.50)
        assert base["revenue"] == original_revenue

    def test_multiple_shocks_combine_independently(self, scenario):
        base = self.make_base()
        shocked = scenario.apply_shocks(base, revenue_shock=-0.10, margin_shock=-2.0, capex_shock=0.05)
        assert shocked["revenue"] == pytest.approx(900.0)
        assert shocked["operating_margin"] == pytest.approx(18.0)
        assert shocked["capex"] == pytest.approx(52.5)


class TestFormatDeltaPct:
    def test_normal_positive_case_shows_percentage(self, scenario):
        result = scenario.format_delta_pct(base=100.0, delta=-10.0)
        assert result == " (-10.0%)"

    def test_negative_base_shows_no_percentage(self, scenario):
        """The real bug this function exists to fix: Net Debt going from
        -12bn to -10.65bn (a net-cash company holding LESS cash than
        before - leverage moving the WRONG way) is arithmetically a
        "-11.9%" change (dividing by a negative base), which reads as
        "improved" when it didn't. Found via a real run on L'Oreal."""
        result = scenario.format_delta_pct(base=-12.0, delta=1.4)
        assert result == ""

    def test_zero_base_shows_no_percentage(self, scenario):
        result = scenario.format_delta_pct(base=0.0, delta=5.0)
        assert result == ""

    def test_sign_flip_shows_no_percentage(self, scenario):
        """Base positive, shocked negative (or vice versa) - a
        percentage of a value that crosses zero is meaningless."""
        result = scenario.format_delta_pct(base=10.0, delta=-15.0)
        assert result == ""

    def test_negative_base_that_stays_negative_still_shows_no_percentage(self, scenario):
        """Guards against a narrower (wrong) fix that only checks for a
        SIGN FLIP - a negative base that stays negative throughout is
        just as misleading as one that flips, and was NOT caught by an
        earlier version of this function that only checked sign parity."""
        result = scenario.format_delta_pct(base=-100.0, delta=-5.0)
        assert result == ""
