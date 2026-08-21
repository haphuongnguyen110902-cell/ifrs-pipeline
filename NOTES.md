\# IFRS Pipeline Project - Running Notes

\_Last updated: 2026-08-20\_



\## Open / Unfinished

\- \[ ] \*\*Nothing parsed/loaded to DB yet except L'Oreal.\*\* All 9 peer companies are downloaded (zips in `data/raw/`) but not yet run through 01 -> 02 -> mapping -> 05 -> 07.

\- \[ ] Downloaded and ready to parse: LVMH (FY2024), Kering (FY2023), EssilorLuxottica (FY2024, file `essilorluxottica\_new.zip`), Puig, Danone, Pernod Ricard, Essity, Moncler (2025), Shell (2025), Amplifon (2025)

\- \[ ] Duplicate files existed after the batch run (single-download + batch-download of the same company) - cleanup given: remove `lvmh\_new.zip`, `kering\_new.zip`, `puig\_new.zip`, keep the other copy of each (confirm this was actually run)

\- \[ ] Mapping file (`ifrs\_concepts\_v0.yaml`) only covers L'Oreal's 100 concepts - every other company will surface new/different XBRL tags needing mapping additions before it can load cleanly

\- \[ ] Not yet connected to a remote GitHub repo - local git commits only so far



\## Key Decisions

\- \*\*Group/consolidated filings only, never subsidiary-level\*\*

\- \*\*Skip Germany and Ireland\*\* - officially documented as unindexed on filings.xbrl.org

\- \*\*Prefer NL over UK when browsing\*\* - UK left the EU ESEF mandate

\- \*\*Novo Nordisk A/S, not "Fonden"\*\*

\- \*\*V1 stops at 8-10 companies, not "all of Europe"\*\*

\- \*\*Different fiscal year ends are fine, just label carefully\*\*

\- \*\*Missing/gappy years across companies are fine\*\*

\- \*\*Built an automated downloader\*\* (`scripts/00\_find\_filing.py`) using filings.xbrl.org's public JSON:API - single-company mode (`--search` / `--entity --download`) AND batch mode (`--batch "Name1,Name2,..."`) that searches+downloads a whole list in one command, printing a summary table at the end



\## Bugs Found \& Fixed

\- \*\*Arelle loaded 0 facts silently on first parse\*\* -> fix: `PackageManager.addPackage()` + `rebuildRemappings()` before loading

\- \*\*Balance Sheet years showed one year ahead\*\* -> fix: subtract 1 day from `end\_date` for instant facts (Arelle's exclusive-boundary convention applies to instant dates too, not just duration end dates)

\- \*\*Statement line items printed alphabetically\*\* -> fix: explicit `STATEMENT\_ORDER` list per statement in `07\_generate\_statements.py`

\- \*\*`00\_find\_filing.py` returned a bogus internal DB number (`14`) instead of a real identifier for some entities\*\* -> fix: extract identifier from the entity's own `relationships.filings.links.related` URL instead of trusting a sometimes-missing top-level attribute



\## Next Steps (pick up here next session)

1\. Confirm duplicate zips were cleaned up (see "Open" above)

2\. Pick ONE peer company (suggest LVMH, clean single-entity search result) and run it through the full pipeline: `01\_explore\_filing.py` -> `02\_explore\_concepts.py` -> compare its concepts against `ifrs\_concepts\_v0.yaml`, extend the mapping for new tags -> `05\_load\_data.py --company "LVMH"` -> `07\_generate\_statements.py --company "LVMH"`

3\. Repeat for the remaining 8 companies once the process is proven on one

4\. Build a side-by-side comps view across all loaded companies

5\. Build the ratio engine (margins, ROIC, cash conversion) per roadmap V1



\## Environment reminders (for picking this back up)

\- Project folder: `C:\\Users\\User\\Downloads\\ifrs-pipeline\\ifrs-pipeline\\`

\- Activate venv each new session: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` then `.venv\\Scripts\\Activate.ps1`

\- `.env` holds the real Neon `DATABASE\_URL` - was reset to a test value once during debugging, confirm it's still the real one before running DB scripts

\- Neon password was exposed once in this chat early on and reset - if anything looks like it can't connect, check the password is current

