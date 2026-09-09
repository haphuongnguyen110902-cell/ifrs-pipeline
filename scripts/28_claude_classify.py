"""
Claude-API-assisted extension-tag classification.

WHY THIS EXISTS AND WHY NOW
----------------------------
ROADMAP.md's "Explicitly deferred" list has carried this since V1: "wait
for the next new company, so it can be built AND tested against real
unmapped tags, not synthetic ones." The WP7 GATE (see PLAN.md's GATE
section) just supplied exactly that: 827 real, unmapped extension-tag
concepts from 40 real companies across 5 countries and sectors the current
642-tag mapping (built entirely from the original 11-company consumer/
luxury/energy universe) had never seen - including banks and a payment
processor, which the GATE's own findings flagged as the likely reason
extension classification failed its ~80% bar (36.5% auto-classified).

WHAT THIS DOES AND DOES NOT DO (per CLAUDE.md's own AI-usage rules)
---------------------------------------------------------------------
CLAUDE.md is explicit: LLMs may assist with "semantic mapping proposals"
but "must never... make an untraceable accounting judgement in production."
So this script:
  - reads the pool of extension tags 10_auto_classify.py/13_batch_prep.py
    could NOT classify deterministically (no authoritative presentation-
    linkbase role - see 13_batch_prep.py's scan_zips()),
  - asks Claude to PROPOSE a statement classification + confidence +
    one-sentence reasoning for each, in batches, via a strict tool schema
    (never free-text parsing - a malformed response fails loudly, it is
    never silently guessed at),
  - writes every proposal to data/mappings/CLAUDE_REVIEW_extensions.yaml
    with full provenance (model name, confidence, reasoning) attached to
    every single entry,
  - NEVER writes to the trusted data/mappings/ifrs_concepts_v0.yaml
    mapping directly.

A human still has to open CLAUDE_REVIEW_extensions.yaml and review it -
exactly the same required step the fully-manual REVIEW_extensions.yaml
has always required - then run the SAME 12_apply_review.py against it.
This script speeds up the first draft (skim high-confidence entries,
scrutinise the low-confidence ones) - it does not remove the human
sign-off gate. 12_apply_review.py now also carries a `classification_source`
field through onto the applied mapping entry, so every concept added via
this path stays permanently traceable as "claude-api-suggested" (vs. the
deterministic classifier's implicit "linkbase-authoritative", or a plain
human entry) - additive, does not touch the existing 642 entries' shape.

USAGE
-----
    # against the WP7 GATE's 40-company sample (no DB, no mapping writes):
    python scripts/28_claude_classify.py --raw-dir data/raw/gate40

    # then, exactly like the fully-manual path:
    #   review data/mappings/CLAUDE_REVIEW_extensions.yaml by hand,
    #   correcting anything that looks wrong (especially confidence: low)
    #   python scripts/12_apply_review.py --review data/mappings/CLAUDE_REVIEW_extensions.yaml

Requires ANTHROPIC_API_KEY in .env (this project's existing DATABASE_URL
pattern - load_dotenv(), sys.exit(1) with a clear message if missing,
rather than failing deep in the SDK).
"""
import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("batch_prep_13", _THIS_DIR / "13_batch_prep.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

VALID_STATEMENTS = ["income_statement", "balance_sheet", "cash_flow", "other"]

SYSTEM_PROMPT = """You are helping classify XBRL/IFRS extension concepts (tags
a company invented for its own filing, not part of the standard ifrs-full:
taxonomy) into the financial statement they belong on. This project uses
exactly four categories:

- income_statement: appears on the Statement of Profit or Loss - revenue,
  expenses, operating profit, finance costs, tax, EPS. Almost always a
  DURATION (flow-over-a-period) concept.
- balance_sheet: appears on the Statement of Financial Position - assets,
  liabilities, equity balances. Almost always an INSTANT (point-in-time)
  concept.
- cash_flow: appears on the Statement of Cash Flows - operating/investing/
  financing cash flows, and adjustments used to reconcile net income to
  cash (e.g. "adjustments for depreciation", "proceeds from...",
  "payments for...").
- other: comprehensive income (OCI) items, the statement of changes in
  equity, or notes/disclosures that are not on one of the three primary
  face statements above (e.g. segment breakdowns, share-based payment
  detail, related-party items).

For each concept you are given its XBRL tag name, its human-readable
label, its `balance` attribute (debit/credit/n/a - a real accounting
signal: assets and expenses are normally debit, liabilities/equity/income
are normally credit), which company's filing it came from (sector context),
and a `hint` describing what this project's own deterministic classifier
already tried and why it wasn't confident enough to auto-classify (e.g.
"periodType=instant" is a real, fairly strong signal toward balance_sheet
on its own; "no role definition" means no signal was available at all).

Classify every concept you are given. Use confidence "low" (with a
reasoning that says why) rather than guessing when the label is genuinely
ambiguous or you don't recognise the accounting concept - a wrong "high"
confidence classification is worse than an honest "low" one, since a human
reviewer will scrutinise "low" entries but may wave through "high" ones."""

CLASSIFY_TOOL = {
    "name": "classify_tags",
    "description": "Record a statement classification for each XBRL extension concept provided.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "xbrl_tag": {"type": "string", "description": "Echo the exact xbrl_tag given"},
                        "statement": {"type": "string", "enum": VALID_STATEMENTS},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reasoning": {"type": "string", "description": "One sentence, cite the label/hint you relied on"},
                    },
                    "required": ["xbrl_tag", "statement", "confidence", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["classifications"],
        "additionalProperties": False,
    },
    "strict": True,
}


def chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build_batch_prompt(entries: list) -> str:
    lines = ["Classify these XBRL extension concepts:\n"]
    for e in entries:
        company = e["used_by"][0] if e.get("used_by") else "unknown"
        lines.append(
            f"- xbrl_tag: {e['xbrl_tag']}\n"
            f"  label: {e['display_label']}\n"
            f"  balance: {e.get('balance', 'n/a')}\n"
            f"  company: {company}\n"
            f"  hint: {e.get('reason') or '(no deterministic signal)'}"
        )
    return "\n".join(lines)


def classify_batch(client, model: str, entries: list, max_retries: int = 3) -> dict:
    """Returns {xbrl_tag: {statement, confidence, reasoning}} for this
    batch. Raises on a bad response rather than guessing - a failed batch
    is reported and skipped by the caller, never silently dropped."""
    prompt = build_batch_prompt(entries)
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "low"},
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_tags"},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError as e:
            last_exc = e
            retry_after = int(e.response.headers.get("retry-after", "30")) if e.response else 30
            print(f"  *** rate limited, backing off {retry_after}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(retry_after)
            continue
        except anthropic.APIStatusError as e:
            last_exc = e
            if e.status_code >= 500:
                print(f"  *** server error {e.status_code}, retrying (attempt {attempt + 1}/{max_retries})")
                time.sleep(5 * (attempt + 1))
                continue
            raise  # 4xx other than 429 - not retryable, surface it
        except anthropic.APIConnectionError as e:
            last_exc = e
            print(f"  *** connection error, retrying (attempt {attempt + 1}/{max_retries})")
            time.sleep(5 * (attempt + 1))
            continue

        for block in response.content:
            if block.type == "tool_use" and block.name == "classify_tags":
                out = {}
                for c in block.input["classifications"]:
                    out[c["xbrl_tag"]] = {
                        "statement": c["statement"],
                        "confidence": c["confidence"],
                        "reasoning": c["reasoning"],
                    }
                return out
        raise RuntimeError(f"No classify_tags tool call in response (stop_reason={response.stop_reason})")

    raise RuntimeError(f"Batch failed after {max_retries} attempts: {last_exc}")


def classify_all(client, model: str, all_review: dict, batch_size: int) -> tuple:
    """Runs classify_batch() over every entry in all_review, in batches.
    Returns (results dict keyed by xbrl_tag, list of xbrl_tags whose batch
    failed - reported, never silently dropped)."""
    entries = list(all_review.values())
    results = {}
    failed_tags = []
    batches = list(chunk(entries, batch_size))
    for i, batch in enumerate(batches, 1):
        print(f"Batch {i}/{len(batches)} ({len(batch)} tags)...")
        try:
            batch_result = classify_batch(client, model, batch)
        except Exception as e:
            print(f"  *** BATCH FAILED, skipping {len(batch)} tags: {e}")
            failed_tags.extend(entry["xbrl_tag"] for entry in batch)
            continue
        results.update(batch_result)
        missing = [e["xbrl_tag"] for e in batch if e["xbrl_tag"] not in batch_result]
        if missing:
            print(f"  *** {len(missing)} tags in this batch got no classification back")
            failed_tags.extend(missing)
    return results, failed_tags


def write_review_file(all_review: dict, results: dict, model: str, out_path: str):
    entries = []
    for qn, entry in all_review.items():
        proposal = results.get(qn)
        out_entry = dict(entry)  # keep xbrl_tag/suggested_key/display_label/balance/reason/used_by
        if proposal:
            out_entry["statement"] = proposal["statement"]
            out_entry["confidence"] = proposal["confidence"]
            out_entry["reasoning"] = proposal["reasoning"]
            out_entry["classification_source"] = "claude-api-suggested"
        else:
            out_entry["statement"] = "REVIEW"
            out_entry["confidence"] = "n/a"
            out_entry["reasoning"] = "Claude classification failed for this tag - classify by hand"
            out_entry["classification_source"] = "human"
        entries.append(out_entry)

    n_high = sum(1 for e in entries if e.get("confidence") == "high")
    n_med = sum(1 for e in entries if e.get("confidence") == "medium")
    n_low = sum(1 for e in entries if e.get("confidence") == "low")
    n_failed = sum(1 for e in entries if e.get("confidence") == "n/a")

    review_data = {
        "_instructions": (
            f"AI-SUGGESTED classifications from {model} - REVIEW before applying, "
            f"especially anything marked confidence: low or n/a. Fix 'statement' for "
            f"anything wrong, then run: "
            f"python scripts/12_apply_review.py --review {out_path}"
        ),
        "_summary": (
            f"{len(entries)} total: {n_high} high-confidence, {n_med} medium, "
            f"{n_low} low, {n_failed} failed (need manual classification)"
        ),
        "needs_review": entries,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(review_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"\nWrote {len(entries)} entries to {out_path}")
    print(f"  high={n_high}  medium={n_med}  low={n_low}  failed={n_failed}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--only", nargs="+", help="Only process these zip filenames")
    ap.add_argument("--out", default="data/mappings/CLAUDE_REVIEW_extensions.yaml")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--batch-size", type=int, default=20)
    args = ap.parse_args()

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not found in .env - add it before running this script.")
        sys.exit(1)

    with open(args.mapping, encoding="utf-8") as f:
        existing = yaml.safe_load(f)
    existing_tags = set()
    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])
    print(f"Existing mapping: {len(existing_tags)} tags\n")

    raw_dir = Path(args.raw_dir)
    if args.only:
        zips = [raw_dir / z for z in args.only if (raw_dir / z).exists()]
    else:
        zips = sorted(raw_dir.glob("*.zip"))

    _, all_review, _ = bp.scan_zips(zips, existing_tags)
    print(f"\n{len(all_review)} extension tags need classification\n")

    if not all_review:
        print("Nothing to classify.")
        sys.exit(0)

    client = anthropic.Anthropic(max_retries=4)
    results, failed_tags = classify_all(client, args.model, all_review, args.batch_size)

    if failed_tags:
        print(f"\n*** {len(failed_tags)} tags could not be classified by Claude - "
              f"written to the review file marked confidence=n/a, classify these by hand:")
        for t in failed_tags[:20]:
            print(f"    {t}")
        if len(failed_tags) > 20:
            print(f"    ... and {len(failed_tags) - 20} more")

    write_review_file(all_review, results, args.model, args.out)
