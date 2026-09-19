"""
tests/test_orchestrator_safety.py

Regression tests for the load-path arguments of scripts/09_batch_load.py and
run_pipeline.py. Each was found by reading the code and then checking it:

  * `run_pipeline.py --mode full/load` reset facts BY DEFAULT (reset=True), i.e.
    the README's headline command ran `09_batch_load.py --reset-facts` for every
    company - the flag behind the project's one data-loss incident;
  * the README/docstring example `--only danone.zip essity.zip` loaded NOTHING
    (09 wanted bare stems and took one value), verified: "'essity.zip' is not in
    data/companies.yaml - skipping";
  * multiple `--only` reached a script that kept only the last;
  * `--country IT` scanned ['FR', 'IT'] (argparse "append" extends its default),
    so IT could never be scanned on its own;
  * 09's docstring said re-loading duplicates facts - false (re-loading Recordati
    left its 221 rows unchanged) - which is what steered people to --reset-facts.
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def load09(load_script):
    return load_script("09_batch_load.py")


@pytest.fixture(scope="module")
def rp():
    spec = importlib.util.spec_from_file_location("run_pipeline", REPO / "run_pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CONFIG = {"essity": {}, "Kering_2023": {}, "heineken": {}, "loreal_2025": {}}


class TestSelectStems:
    def test_no_filter_means_every_company(self, load09):
        assert load09.select_stems(None, CONFIG) == (list(CONFIG), [])

    def test_a_bare_stem_works(self, load09):
        assert load09.select_stems(["essity"], CONFIG) == (["essity"], [])

    def test_the_readme_example_with_a_zip_suffix_now_works(self, load09):
        assert load09.select_stems(["essity.zip"], CONFIG) == (["essity"], [])

    def test_matching_ignores_case_and_returns_the_real_key(self, load09):
        assert load09.select_stems(["kering_2023.ZIP"], CONFIG)[0] == ["Kering_2023"]

    def test_a_path_is_reduced_to_its_filename(self, load09):
        assert load09.select_stems(["data/raw/gate40/heineken.zip"], CONFIG)[0] == ["heineken"]

    def test_several_stems_are_all_kept_in_order_without_duplicates(self, load09):
        stems, unknown = load09.select_stems(["heineken", "essity.zip", "heineken"], CONFIG)
        assert stems == ["heineken", "essity"] and unknown == []

    def test_unknown_names_are_reported_not_silently_dropped(self, load09):
        stems, unknown = load09.select_stems(["essity", "nope.zip"], CONFIG)
        assert stems == ["essity"] and unknown == ["nope.zip"]


class TestResetScope:
    def test_reset_of_everything_is_refused(self, load09):
        msg = load09.reset_scope_error(True, None, False)
        assert msg and "--only" in msg and "--yes-reset-all" in msg

    def test_reset_of_a_named_company_is_allowed(self, load09):
        assert load09.reset_scope_error(True, ["essity"], False) is None

    def test_reset_of_everything_needs_the_explicit_confirmation(self, load09):
        assert load09.reset_scope_error(True, None, True) is None

    def test_no_reset_is_never_an_error(self, load09):
        assert load09.reset_scope_error(False, None, False) is None
        assert load09.reset_scope_error(False, ["essity"], False) is None

    def test_the_loader_refuses_before_touching_anything(self):
        """The refusal must come before the database is opened."""
        source = (REPO / "scripts" / "09_batch_load.py").read_text(encoding="utf-8")
        assert source.index("reset_scope_error(args.reset_facts") < source.index("psycopg2.connect(")

    def test_the_docstring_no_longer_claims_reloading_duplicates_facts(self):
        source = (REPO / "scripts" / "09_batch_load.py").read_text(encoding="utf-8")
        assert "WILL create\nduplicate fact rows" not in source.replace("\r\n", "\n")
        assert "does NOT duplicate" in source


class TestPipelineLoadCommand:
    def test_default_is_no_reset(self, rp):
        assert "--reset-facts" not in rp.build_load_command()

    def test_step_load_defaults_to_no_reset(self, rp):
        import inspect
        assert inspect.signature(rp.step_load).parameters["reset"].default is False

    def test_several_stems_are_forwarded_as_one_only_flag(self, rp):
        cmd = rp.build_load_command(only=["danone", "essity"])
        assert cmd[-3:] == ["--only", "danone", "essity"]
        assert cmd.count("--only") == 1        # a repeated --only would keep just the last

    def test_reset_is_forwarded_only_when_asked(self, rp):
        assert "--reset-facts" in rp.build_load_command(only=["essity"], reset=True)


class TestPipelineArguments:
    def parse(self, rp, *argv):
        return rp.build_parser().parse_args(list(argv))

    def test_load_mode_does_not_reset_by_default(self, rp):
        assert self.parse(rp, "--mode", "load").reset_facts is False

    def test_reset_facts_is_opt_in(self, rp):
        assert self.parse(rp, "--mode", "load", "--only", "essity", "--reset-facts").reset_facts is True

    def test_the_old_no_reset_flag_is_still_accepted(self, rp):
        assert self.parse(rp, "--mode", "load", "--no-reset").no_reset is True

    def test_a_single_country_is_just_that_country(self, rp):
        """It used to be ['FR', 'IT']."""
        assert self.parse(rp, "--mode", "discover", "--country", "IT").country == ["IT"]

    def test_several_countries_are_all_kept(self, rp):
        assert self.parse(rp, "--country", "IT", "--country", "SE").country == ["IT", "SE"]

    def test_no_country_falls_back_to_france_in_main(self, rp):
        assert self.parse(rp).country is None
        assert "args.country or [\"FR\"]" in (REPO / "run_pipeline.py").read_text(encoding="utf-8")

    def test_only_accepts_several_and_zip_names(self, rp):
        assert self.parse(rp, "--only", "danone.zip", "essity").only == ["danone.zip", "essity"]

    def test_reset_without_only_is_rejected_by_main(self, rp, monkeypatch):
        monkeypatch.setattr(rp.sys, "argv", ["run_pipeline.py", "--mode", "load", "--reset-facts"])
        with pytest.raises(SystemExit) as exc:
            rp.main()
        assert exc.value.code == 2          # argparse error, before any environment or database check
