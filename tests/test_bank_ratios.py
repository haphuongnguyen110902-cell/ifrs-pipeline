"""
tests/test_bank_ratios.py

Banks and insurers were only ever blanked: gross margin, working-capital days, cash conversion, ROIC and net debt mean
nothing for a lender (gate_financial_ratios), and nothing took their place. These are the measures a bank's or an
insurer's own IFRS statements support (FINANCIAL_RATIO_META), stored for financial companies only.

The expected values are hand-checked against printed lines of two reports in data/raw/gate40 (Phase 3, read in
memory - neither bank is loaded yet):
  BNP Paribas FY2025 - revenues 51,223, gross operating income 19,849, IFRS 9 impairment 3,350, customer loans at
    amortised cost 897,358, customer deposits at amortised cost 1,075,564, insurance revenue 10,270, insurance
    service expenses 8,101 (EUR M);
  Svenska Handelsbanken FY2024 - total income 62,345, total expenses 25,209, net credit losses -601 (a reversal),
    loans to the public 2,297,878, deposits from the public 1,310,739, equity 210,027, total assets 3,539,173 (SEK M).
No database.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def bank(**cols):
    return pd.DataFrame([{"company": "B", "company_id": 9, "year": 2025, **cols}])


BNP = dict(revenue_and_operating_income=51_223.0, operating_profit_before_impairment=19_849.0,
           impairment_loss_ifrs_9=3_350.0, loans_and_advances_to_customers=897_358.0,
           deposits_from_customers=1_075_564.0, insurance_revenue=10_270.0, insurance_service_expenses=8_101.0)
SHB = dict(revenue_and_operating_income=62_345.0, expense_by_nature=25_209.0, impairment_loss_ifrs_9=-601.0,
           loans_and_advances_to_customers=2_297_878.0, deposits_from_customers=1_310_739.0,
           equity=210_027.0, assets=3_539_173.0)


class TestMeasures:
    def test_cost_income_from_the_expense_total(self, r11):
        assert r11.compute_ratios(bank(**SHB))["cost_income_ratio"].iloc[0] == pytest.approx(100 * 25_209 / 62_345)

    def test_cost_income_from_the_pre_impairment_subtotal(self, r11):
        """BNP prints no expense total: revenues less gross operating income are its operating expenses."""
        r = r11.compute_ratios(bank(**BNP))
        assert r["cost_income_ratio"].iloc[0] == pytest.approx(100 * (51_223 - 19_849) / 51_223)       # 61.2%

    def test_cost_of_risk_in_basis_points_keeps_a_reversal_negative(self, r11):
        assert r11.compute_ratios(bank(**BNP))["cost_of_risk"].iloc[0] == pytest.approx(3_350 / 897_358 * 1e4)
        assert r11.compute_ratios(bank(**SHB))["cost_of_risk"].iloc[0] == pytest.approx(-601 / 2_297_878 * 1e4)

    def test_loan_to_deposit_and_equity_to_assets(self, r11):
        r = r11.compute_ratios(bank(**SHB))
        assert r["loan_to_deposit"].iloc[0] == pytest.approx(100 * 2_297_878 / 1_310_739)
        assert r["equity_to_assets"].iloc[0] == pytest.approx(100 * 210_027 / 3_539_173)

    def test_insurance_service_ratio_ifrs_17(self, r11):
        assert r11.compute_ratios(bank(**BNP))["insurance_service_ratio"].iloc[0] == pytest.approx(100 * 8_101 / 10_270)

    def test_a_missing_line_is_blank_never_zero(self, r11):
        r = r11.compute_ratios(bank(revenue_and_operating_income=51_223.0))
        assert pd.isna(r["cost_income_ratio"].iloc[0]) and pd.isna(r["loan_to_deposit"].iloc[0])


class TestStorage:
    def test_only_financial_companies_and_only_measures_they_have(self, r11):
        ratios = r11.compute_ratios(pd.concat([bank(**SHB), bank(**{**BNP, "company_id": 3}),
                                               bank(company_id=5, equity=1.0, assets=4.0)], ignore_index=True))
        store = r11.financial_ratios_to_store(ratios, {9: "bank", 5: "payments"})
        assert (9, "cost_income_ratio") in store and (9, "insurance_service_ratio") not in store
        assert not any(cid == 3 for cid, _ in store)                     # not a financial company: nothing stored
        assert store & {(5, n) for n in r11.FINANCIAL_RATIO_META} == {(5, "equity_to_assets")}

    def test_basis_points_are_formatted_as_such(self, r11):
        assert r11.format_ratio(37.3, "bp") == "37bp"
