"""
tests/test_forensics_financial_gating.py

Two defects found while deepening the history of the universe companies:

1. Adyen (a payment processor, flagged `reporting_model: financial`) got forensic flags
   that describe a definition break, not performance: its stored revenue is EUR 8,936M for
   FY2022 and EUR 1,863M for FY2023 (gross before, net after), so the rules raised
   "revenue -79.1%" and "operating margin 7.4% -> 35.3%"; "net cash 11x operating profit"
   describes client money. Financial companies are now excluded, using the same
   classification the ratio engine uses to blank their ratios.

2. 15_forensics.py crashed with UnicodeEncodeError when its output was piped or redirected on
   Windows (cp1252 cannot encode the severity emoji) - AFTER computing the flags and BEFORE
   saving them. A caller that filtered the output saw success lines and nothing was written.

No database.
"""
import io

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def f15(load_script):
    return load_script("15_forensics.py")


def flags_frame(*rows):
    return pd.DataFrame([{"company": c, "year": y, "flag_id": fid, "severity": sev}
                         for c, y, fid, sev in rows])


class TestFinancialSuppression:
    def test_only_the_financial_companys_flags_are_dropped(self, f15):
        flags = flags_frame(("Adyen", 2023, "REVENUE_DECELERATION", "medium"),
                            ("Heineken", 2022, "MARGIN_COMPRESSION", "medium"),
                            ("Adyen", 2025, "CASH_CONVERSION_DROP", "high"))
        kept, dropped = f15.drop_flags_for_financial_companies(flags, {"Adyen": "reporting_model: financial"})
        assert dropped == ["Adyen"]
        assert list(kept["company"]) == ["Heineken"] and kept.columns.tolist() == flags.columns.tolist()

    def test_nothing_financial_means_nothing_changes(self, f15):
        flags = flags_frame(("Heineken", 2022, "MARGIN_COMPRESSION", "medium"))
        kept, dropped = f15.drop_flags_for_financial_companies(flags, {})
        assert dropped == [] and kept.equals(flags)
        kept, dropped = f15.drop_flags_for_financial_companies(flags, {"Adyen": "x"})   # a name with no flags
        assert dropped == [] and len(kept) == 1

    def test_an_empty_frame_is_returned_as_is(self, f15):
        empty = flags_frame()
        kept, dropped = f15.drop_flags_for_financial_companies(empty, {"Adyen": "x"})
        assert kept.empty and dropped == []

    def test_dropping_every_flag_keeps_the_columns_so_the_stale_row_cleanup_still_runs(self, f15):
        """save_to_db needs a frame with a 'company' column; an all-dropped frame must still have one."""
        kept, _ = f15.drop_flags_for_financial_companies(flags_frame(("Adyen", 2023, "X", "low")), {"Adyen": "x"})
        assert kept.empty and "company" in kept.columns

    def test_the_main_block_suppresses_before_printing_and_saving(self, f15):
        src = open(f15.__file__, encoding="utf-8").read()
        main = src[src.index('if __name__ == "__main__":'):]
        assert main.index("drop_flags_for_financial_companies") < main.index("print_summary(") < main.index("save_to_db(")


class TestUnencodableOutput:
    EMOJI = "\U0001f7e1 MEDIUM"          # the yellow circle print_summary writes

    def cp1252_stream(self):
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    def test_the_original_crash_is_real(self):
        """Without the fix a cp1252 stream refuses the emoji - the exact failure seen live."""
        with pytest.raises(UnicodeEncodeError):
            print(self.EMOJI, file=self.cp1252_stream(), flush=True)

    def test_after_the_fix_the_same_stream_prints_instead_of_crashing(self, f15):
        s = self.cp1252_stream()
        f15.ensure_printable_output(s)
        print(self.EMOJI, file=s, flush=True)
        assert b"MEDIUM" in s.buffer.getvalue()

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self, f15):
        f15.ensure_printable_output(io.StringIO())      # StringIO has no reconfigure: must not raise
