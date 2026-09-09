"""
tests/test_claude_classify.py

Regression tests for scripts/28_claude_classify.py - the Claude-API-
assisted first draft for extension tags the deterministic classifier
(13_batch_prep.py's scan_zips) couldn't handle on its own.

No real API calls here (CI has no ANTHROPIC_API_KEY, same constraint
tests.yml already has for DATABASE_URL) - the Claude client is mocked at
the classify_batch() boundary, since the actual GLEIF/OpenFIGI-style "does
this really work against the live API" verification for this script is
`python scripts/28_claude_classify.py --raw-dir data/raw/gate40`, run by
hand against real data (see PLAN.md's GATE section), not something to
fake in a unit test. What IS unit-tested here is everything downstream of
a response: prompt building, batching/failure bookkeeping, and the
review-file shape/provenance fields - all pure, all synthetic data.
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def cc(load_script):
    return load_script("28_claude_classify.py")


def make_review_entry(tag, label="Some Label", balance="debit", reason="no role definition", company="TestCo"):
    return {
        "xbrl_tag": tag,
        "suggested_key": tag.split(":")[-1].lower(),
        "display_label": label,
        "statement": "REVIEW",
        "balance": balance,
        "reason": reason,
        "used_by": [company],
    }


# ---------------------------------------------------------------- prompt building

def test_build_batch_prompt_includes_all_signals(cc):
    entries = [make_review_entry("ady:NetNonInterestRevenue", label="Net Non-Interest Revenue",
                                  balance="credit", reason="no role definition", company="Adyen")]
    prompt = cc.build_batch_prompt(entries)
    assert "ady:NetNonInterestRevenue" in prompt
    assert "Net Non-Interest Revenue" in prompt
    assert "credit" in prompt
    assert "Adyen" in prompt
    assert "no role definition" in prompt


def test_build_batch_prompt_handles_missing_reason(cc):
    entry = make_review_entry("x:Foo", reason="")
    prompt = cc.build_batch_prompt([entry])
    assert "(no deterministic signal)" in prompt


# ---------------------------------------------------------------- classify_batch (mocked client)

def _fake_tool_response(classifications):
    """Mimics the shape of an anthropic Message with one tool_use block."""
    block = SimpleNamespace(type="tool_use", name="classify_tags",
                             input={"classifications": classifications})
    return SimpleNamespace(content=[block], stop_reason="tool_use")


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self.messages = _FakeMessages(response, exc)


def test_classify_batch_parses_tool_use_response(cc):
    entries = [make_review_entry("a:Tag1"), make_review_entry("a:Tag2")]
    response = _fake_tool_response([
        {"xbrl_tag": "a:Tag1", "statement": "balance_sheet", "confidence": "high", "reasoning": "instant, asset-like"},
        {"xbrl_tag": "a:Tag2", "statement": "cash_flow", "confidence": "medium", "reasoning": "adjustments-for pattern"},
    ])
    client = _FakeClient(response=response)
    result = cc.classify_batch(client, "claude-opus-5", entries)
    assert result["a:Tag1"] == {"statement": "balance_sheet", "confidence": "high", "reasoning": "instant, asset-like"}
    assert result["a:Tag2"]["statement"] == "cash_flow"


def test_classify_batch_raises_when_no_tool_call_present(cc):
    entries = [make_review_entry("a:Tag1")]
    # a response with only a text block, no tool_use - must raise, never
    # silently return an empty/guessed result
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="I decline")], stop_reason="end_turn")
    client = _FakeClient(response=response)
    with pytest.raises(RuntimeError):
        cc.classify_batch(client, "claude-opus-5", entries)


# ---------------------------------------------------------------- classify_all batching/failure bookkeeping

def test_classify_all_records_failed_tags_without_dropping_them(cc, monkeypatch):
    entries_dict = {
        "a:Tag1": make_review_entry("a:Tag1"),
        "a:Tag2": make_review_entry("a:Tag2"),
        "a:Tag3": make_review_entry("a:Tag3"),
    }

    calls = {"n": 0}

    def fake_classify_batch(client, model, batch, max_retries=3):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated API failure")
        return {e["xbrl_tag"]: {"statement": "other", "confidence": "low", "reasoning": "x"} for e in batch}

    monkeypatch.setattr(cc, "classify_batch", fake_classify_batch)
    results, failed = cc.classify_all(client=None, model="claude-opus-5", all_review=entries_dict, batch_size=1)

    # exactly one of the three single-entry batches was made to fail
    assert len(failed) == 1
    assert len(results) == 2
    assert set(results) | set(failed) == set(entries_dict)


# ---------------------------------------------------------------- write_review_file shape/provenance

def test_write_review_file_shape_and_provenance(cc, tmp_path):
    all_review = {
        "a:Tag1": make_review_entry("a:Tag1"),
        "a:Tag2": make_review_entry("a:Tag2"),
    }
    results = {
        "a:Tag1": {"statement": "income_statement", "confidence": "high", "reasoning": "clearly a P&L line"},
        # a:Tag2 deliberately missing - simulates a failed tag
    }
    out_path = tmp_path / "CLAUDE_REVIEW_extensions.yaml"
    cc.write_review_file(all_review, results, "claude-opus-5", str(out_path))

    with open(out_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = {e["xbrl_tag"]: e for e in data["needs_review"]}
    assert entries["a:Tag1"]["statement"] == "income_statement"
    assert entries["a:Tag1"]["confidence"] == "high"
    assert entries["a:Tag1"]["classification_source"] == "claude-api-suggested"

    # the failed tag must NEVER be silently classified - stays REVIEW,
    # explicitly marked as needing a human, not attributed to Claude
    assert entries["a:Tag2"]["statement"] == "REVIEW"
    assert entries["a:Tag2"]["confidence"] == "n/a"
    assert entries["a:Tag2"]["classification_source"] == "human"

    assert "claude-opus-5" in data["_instructions"]
    assert "1 high-confidence" in data["_summary"]


# ---------------------------------------------------------------- 12_apply_review.py provenance passthrough

def test_apply_review_carries_classification_source_through(tmp_path):
    """End-to-end (real subprocess, no mocking) check that a Claude-suggested
    entry's provenance survives into the applied mapping, while a plain
    human-style entry with no classification_source stays exactly as
    every entry has always looked - additive, not a shape change."""
    mapping_path = tmp_path / "mapping.yaml"
    review_path = tmp_path / "review.yaml"

    mapping_path.write_text(yaml.dump({
        "income_statement": {}, "balance_sheet": {}, "cash_flow": {}, "other": {},
    }), encoding="utf-8")

    review_path.write_text(yaml.dump({
        "needs_review": [
            {
                "xbrl_tag": "a:ClaudeTag", "suggested_key": "claude_tag",
                "display_label": "Claude Tag", "statement": "cash_flow",
                "classification_source": "claude-api-suggested",
            },
            {
                "xbrl_tag": "a:HumanTag", "suggested_key": "human_tag",
                "display_label": "Human Tag", "statement": "other",
            },
        ]
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "12_apply_review.py"),
         "--review", str(review_path), "--mapping", str(mapping_path)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    with open(mapping_path, encoding="utf-8") as f:
        applied = yaml.safe_load(f)

    assert applied["cash_flow"]["claude_tag"]["classification_source"] == "claude-api-suggested"
    assert "classification_source" not in applied["other"]["human_tag"]
