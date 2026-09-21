"""
tests/test_credit_skips_financial.py

Applying the net-debt fix re-ran 24_credit.py for every company. Adyen (a payment processor, declared
`reporting_model: financial`) had just gained its full history, so the stage produced seven credit rows saying
"Net cash -9.5x" for it - client money presented as surplus. Financial companies are now skipped, using the same
classification that blanks their ratios. No database.
"""
import pytest


@pytest.fixture(scope="module")
def c24(load_script):
    return load_script("24_credit.py")


def test_only_the_financial_company_is_skipped(c24):
    kept, skipped = c24.split_financial(["Adyen", "Danone", "Heineken"], {"Adyen": "reporting_model: financial"})
    assert kept == ["Danone", "Heineken"]
    assert skipped == [("Adyen", "reporting_model: financial")]


def test_nothing_financial_means_nothing_skipped(c24):
    kept, skipped = c24.split_financial(["Danone", "LVMH"], {})
    assert kept == ["Danone", "LVMH"] and skipped == []


def test_a_financial_name_that_is_not_in_the_run_is_ignored(c24):
    kept, skipped = c24.split_financial(["Danone"], {"Adyen": "x"})
    assert kept == ["Danone"] and skipped == []


def test_order_is_preserved(c24):
    kept, _ = c24.split_financial(["C", "A", "B"], {"A": "x"})
    assert kept == ["C", "B"]


def test_the_main_block_skips_before_computing_anything(c24):
    src = open(c24.__file__, encoding="utf-8").read()
    main = src[src.index('if __name__ == "__main__":'):]
    assert main.index("split_financial(") < main.index("compute_credit_metrics(")
