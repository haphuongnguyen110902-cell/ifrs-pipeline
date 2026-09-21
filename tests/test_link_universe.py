"""
tests/test_link_universe.py

scripts/31_link_universe.py connects `universe_membership` to `company`, and
19_valuation.populate_sector_std now falls back to the DB ticker. Found by the
full review: universe_membership.company_id was NULL on all 217 rows, and the 5
companies loaded from the universe (ASM, Adyen, Heineken, Recordati, Schneider)
had no ticker/LEI/currency/sector, so they showed Sector "None" and blank
valuation columns on the dashboard's landing screener.

No database and no network: matching/confirmation are pure functions, the writes
go through a recording fake engine, and yfinance is monkeypatched.
"""
import pytest


@pytest.fixture(scope="module")
def link(load_script):
    return load_script("31_link_universe.py")


@pytest.fixture(scope="module")
def valuation(load_script):
    return load_script("19_valuation.py")


def uni(entity, name, country="Netherlands", ticker="X.AS", ccy="EUR"):
    return {"entity_identifier": entity, "name": name, "country": country,
            "ticker": ticker, "ticker_currency": ccy}


def co(cid, name, country="Netherlands", lei=None, ticker=None, ccy=None):
    return {"company_id": cid, "name": name, "country": country, "lei": lei,
            "ticker": ticker, "ticker_currency": ccy}


def yf_says(name, currency="EUR"):
    return lambda ticker: {"name": name, "currency": currency}


# ---------------------------------------------------------------- names

class TestNames:
    def test_accents_punctuation_and_legal_forms_do_not_matter(self, link):
        assert link.normalise_name("L'Oréal S.A.") == link.normalise_name("L'Oreal") == "loreal"
        assert link.normalise_name("Heineken N.V.") == "heineken"
        assert link.normalise_name("ASM International N.V.") == "asm international"

    def test_a_share_class_is_part_of_the_identity(self, link):
        assert link.normalise_name("Essity B") != link.normalise_name("Essity")

    def test_missing_names_are_empty_not_a_crash(self, link):
        assert link.normalise_name(None) == "" and link.normalise_name(float("nan")) == ""


# ---------------------------------------------------------------- matching

class TestMatching:
    def test_lei_match_wins(self, link):
        u = [uni("LEI1", "Something Else Entirely"), uni("LEI2", "Heineken")]
        row, how, _ = link.match_universe(co(1, "Heineken", lei="LEI1"), u)
        assert row["entity_identifier"] == "LEI1" and how == "lei"

    def test_name_and_country_match(self, link):
        row, how, _ = link.match_universe(co(1, "Heineken"), [uni("LEI2", "Heineken N.V.")])
        assert row["entity_identifier"] == "LEI2" and how == "name+country"

    def test_same_name_in_another_country_is_not_the_same_company(self, link):
        """Shell is UK in `company` but 'Shell plc / Netherlands' in the universe (its Amsterdam listing)."""
        row, _, reason = link.match_universe(co(1, "Shell", country="United Kingdom"),
                                             [uni("N", "Shell plc", country="Netherlands")])
        assert row is None and "no universe entity" in reason

    def test_a_share_class_variant_is_not_auto_linked(self, link):
        """Essity vs 'Essity B': the resolver's known A/B share-class problem - refuse, do not guess."""
        row, _, _ = link.match_universe(co(1, "Essity", country="Sweden"),
                                        [uni("NO_LEI:Essity B", "Essity B", country="Sweden")])
        assert row is None

    def test_two_entities_with_the_same_name_are_ambiguous_not_arbitrary(self, link):
        u = [uni("A", "Acme"), uni("B", "Acme N.V.")]
        row, _, reason = link.match_universe(co(1, "Acme"), u)
        assert row is None and "2 universe entities" in reason

    def test_no_match_is_reported(self, link):
        row, _, reason = link.match_universe(co(1, "Puig Brands", country="Spain"), [uni("A", "Adyen")])
        assert row is None and reason


# ---------------------------------------------------------------- independent confirmation

class TestConfirmation:
    def test_confirmed_when_yfinance_name_contains_the_company_name(self, link):
        assert link.confirm_instrument("Schneider Electric", {"name": "Schneider Electric S.E.", "currency": "EUR"}, None) == ("EUR", None)
        assert link.confirm_instrument("Recordati", {"name": "Recordati Industria Chimica e Farmaceutica S.p.A.", "currency": "EUR"}, "EUR")[0] == "EUR"

    def test_a_different_company_is_refused(self, link):
        ccy, why = link.confirm_instrument("Adyen", {"name": "Booking Holdings", "currency": "USD"}, "USD")
        assert ccy is None and "does not contain" in why

    def test_KNOWN_LIMIT_a_sister_entity_whose_name_extends_ours_is_not_caught(self, link):
        """Documented, not fixed: the check is 'all our name words appear in yfinance's name', so it
        catches a wrong company but cannot tell Heineken N.V. (HEIA) from Heineken Holding N.V. (HEIO).
        The universe's own ticker is what keeps that from mattering; this test records the limit so
        nobody mistakes the confirmation for stronger proof than it is."""
        ccy, _ = link.confirm_instrument("Heineken", {"name": "Heineken Holding N.V.", "currency": "EUR"}, "EUR")
        assert ccy == "EUR"

    def test_a_currency_conflict_is_refused(self, link):
        ccy, why = link.confirm_instrument("Shell", {"name": "Shell plc", "currency": "GBp"}, "USD")
        assert ccy is None and "conflict" in why

    def test_no_answer_from_yfinance_is_refused(self, link):
        assert link.confirm_instrument("Adyen", None, "EUR")[0] is None
        assert link.confirm_instrument("Adyen", {"name": "", "currency": "EUR"}, "EUR")[0] is None

    def test_unknown_currency_everywhere_is_refused_not_defaulted(self, link):
        ccy, why = link.confirm_instrument("Adyen", {"name": "Adyen N.V.", "currency": None}, None)
        assert ccy is None and "unknown" in why

    def test_currency_falls_back_to_yfinance_when_the_universe_has_none(self, link):
        """Schneider: the universe's national-index row had a ticker but no currency."""
        assert link.confirm_instrument("Schneider Electric", {"name": "Schneider Electric SE", "currency": "EUR"}, None)[0] == "EUR"


# ---------------------------------------------------------------- plans

class TestPlan:
    def test_a_fresh_company_gets_lei_ticker_currency_and_source(self, link):
        p = link.plan_company(co(7, "Heineken"), [uni("LEI9", "Heineken N.V.", ticker="HEIA.AS")], yf_says("Heineken N.V."))
        assert p["link"] == "LEI9"
        assert p["updates"] == {"lei": "LEI9", "ticker": "HEIA.AS", "ticker_currency": "EUR",
                                "ticker_source": "universe_membership"}

    def test_a_placeholder_identifier_is_never_stored_as_an_lei(self, link):
        p = link.plan_company(co(7, "Schneider Electric", country="France"),
                              [uni("NO_LEI:Schneider Electric", "Schneider Electric", country="France", ticker="SU.PA", ccy=None)],
                              yf_says("Schneider Electric S.E."))
        assert "lei" not in p["updates"] and p["updates"]["ticker"] == "SU.PA" and p["updates"]["ticker_currency"] == "EUR"

    def test_existing_values_are_left_alone(self, link):
        p = link.plan_company(co(7, "Heineken", lei="MINE", ticker="HEIO.AS", ccy="EUR"),
                              [uni("MINE", "Heineken", ticker="HEIA.AS")], yf_says("Heineken N.V."))
        assert p["updates"] == {} and p["link"] == "MINE"
        assert any("already set" in n for n in p["notes"])

    def test_an_unconfirmed_ticker_is_not_written_but_the_link_still_is(self, link):
        p = link.plan_company(co(7, "Heineken"), [uni("LEI9", "Heineken", ticker="HEIA.AS")], lambda t: None)
        assert p["link"] == "LEI9" and "ticker" not in p["updates"]
        assert any("NOT written" in n for n in p["notes"])

    def test_a_universe_row_without_a_ticker_writes_only_the_lei(self, link):
        p = link.plan_company(co(7, "Heineken"), [uni("LEI9", "Heineken", ticker=None)], yf_says("Heineken"))
        assert p["updates"] == {"lei": "LEI9"}

    def test_no_match_plans_nothing(self, link):
        p = link.plan_company(co(7, "Puig Brands", country="Spain"), [uni("A", "Adyen")], yf_says("x"))
        assert p["link"] is None and p["updates"] == {} and p["notes"]


# ---------------------------------------------------------------- writes

class FakeConn:
    def __init__(self, log, rowcount):
        self.log, self.rowcount = log, rowcount

    def execute(self, stmt, params=None):
        self.log.append((" ".join(str(stmt).split()), dict(params or {})))
        rc = self.rowcount

        class R:
            rowcount = rc
        return R()


class FakeEngine:
    def __init__(self, rowcount=1):
        self.log, self.rowcount = [], rowcount

    def begin(self):
        engine = self

        class Ctx:
            def __enter__(self):
                return FakeConn(engine.log, engine.rowcount)

            def __exit__(self, *a):
                return False
        return Ctx()


class TestApply:
    def plan(self, link):
        return link.plan_company(co(7, "Heineken"), [uni("LEI9", "Heineken", ticker="HEIA.AS")], yf_says("Heineken"))

    def test_writes_only_where_null_so_nothing_existing_is_overwritten(self, link):
        eng = FakeEngine()
        out = link.apply_plans(eng, [self.plan(link)])
        sqls = [s for s, _ in eng.log]
        assert any(s.startswith("UPDATE universe_membership SET company_id") and "company_id IS NULL" in s for s in sqls)
        assert any(s.startswith("UPDATE company SET ticker") and "ticker IS NULL" in s for s in sqls)
        assert any(s.startswith("UPDATE company SET lei") and "lei IS NULL" in s for s in sqls)
        assert out == {"universe_rows_linked": 1, "companies_filled": 1}

    def test_ticker_currency_and_source_are_written_as_one_unit(self, link):
        """A company marked ticker_source='UNRESOLVED' must not keep that marker next to a real ticker."""
        eng = FakeEngine()
        link.apply_plans(eng, [self.plan(link)])
        (sql, params), = [(s, p) for s, p in eng.log if s.startswith("UPDATE company SET ticker")]
        assert "ticker_currency = :ticker_currency" in sql and "ticker_source = :ticker_source" in sql
        assert params["ticker_source"] == "universe_membership" and params["ticker"] == "HEIA.AS"

    def test_a_second_run_reports_nothing_changed(self, link):
        out = link.apply_plans(FakeEngine(rowcount=0), [self.plan(link)])
        assert out == {"universe_rows_linked": 0, "companies_filled": 0}

    def test_an_unlinked_company_writes_nothing(self, link):
        eng = FakeEngine()
        link.apply_plans(eng, [link.plan_company(co(7, "Puig", country="Spain"), [], yf_says("x"))])
        assert eng.log == []


# ---------------------------------------------------------------- sector via the DB ticker

class SectorConn:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.log.append((sql, dict(params or {})))

        class R:
            def fetchall(inner):
                return self.rows
        return R()


class SectorEngine:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def begin(self):
        engine = self

        class Ctx:
            def __enter__(self):
                return SectorConn(engine.rows, engine.log)

            def __exit__(self, *a):
                return False
        return Ctx()


class TestSectorFromDbTicker:
    @pytest.fixture
    def yf_calls(self, valuation, monkeypatch):
        calls = []

        class FakeTicker:
            def __init__(self, t):
                calls.append(t)
                self.info = {"sector": None if t == "NOSECTOR" else f"Sector of {t}"}
        monkeypatch.setattr(valuation.yf, "Ticker", FakeTicker)
        return calls

    def test_a_company_outside_TICKER_MAP_gets_a_sector_from_its_db_ticker(self, valuation, yf_calls):
        eng = SectorEngine([("Heineken", "HEIA.AS")])
        assert valuation.populate_sector_std(eng) == 1
        assert yf_calls == ["HEIA.AS"]
        (sql, params), = [(s, p) for s, p in eng.log if s.startswith("UPDATE company SET sector_std")]
        assert params == {"s": "Sector of HEIA.AS", "n": "Heineken"}

    def test_TICKER_MAP_stays_authoritative_over_the_db_ticker(self, valuation, yf_calls):
        """Essity: the resolver stores an A-share ticker, TICKER_MAP has the B-share the facts belong to."""
        valuation.populate_sector_std(SectorEngine([("Essity", "ESSITY-A.ST")]))
        assert yf_calls == [valuation.TICKER_MAP["Essity"][0]]

    def test_a_company_with_no_ticker_anywhere_is_skipped_not_guessed(self, valuation, yf_calls):
        eng = SectorEngine([("Puig Unknown", None)])
        assert valuation.populate_sector_std(eng) == 0 and yf_calls == []
        assert not [s for s, _ in eng.log if s.startswith("UPDATE")]

    def test_no_sector_from_yfinance_leaves_it_null(self, valuation, yf_calls):
        eng = SectorEngine([("Odd Co", "NOSECTOR")])
        assert valuation.populate_sector_std(eng) == 0

    def test_only_null_sectors_are_queried_unless_forced(self, valuation, yf_calls):
        eng = SectorEngine([])
        valuation.populate_sector_std(eng)
        valuation.populate_sector_std(eng, force=True)
        selects = [s for s, _ in eng.log if s.startswith("SELECT")]
        assert "WHERE sector_std IS NULL" in selects[0] and "WHERE" not in selects[1]
