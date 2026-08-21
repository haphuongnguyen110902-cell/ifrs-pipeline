# TOMORROW - Your Complete Instruction File

Open this file, start at the top, work down. Everything you need is here.

**Three rules:**
1. Do **one step at a time**. Don't read ahead and don't batch commands.
2. After each step there's a **"YOU SHOULD SEE"** line. If what's on your
   screen doesn't match, **stop** and ask before continuing.
3. If something goes wrong, **screenshot the whole window** and ask. Don't
   guess at fixes - the error message tells us exactly what's wrong, and
   reading it is faster than trial and error.

---

# HOW TODAY IS PRIORITISED (and why)

Tasks are ordered by this rule: **protect first, then build foundations, then
verify, then extend.** Never build on top of something you haven't verified.

| Tier | Parts | Why this order |
|---|---|---|
| **MUST DO** | 0, A, B, C, D, E | Protects your work, then gets all 11 companies into the database. Everything else depends on this. |
| **SHOULD DO** | F, G | Verifies the data is actually correct. Doing analysis on unverified data wastes the analysis. |
| **IF TIME** | H | Improves how future companies get classified. Valuable but not blocking. |

**If you only get through Tier 1, that's a good day.** Stopping after Part E
leaves everything in a clean, safe state.

> **WHY verification comes before analysis** - suppose a tag is mapped to the
> wrong concept and L'Oréal's gross margin comes out as 43% instead of 76%.
> If you compute ratios first, you'd build charts, comparisons, maybe a whole
> DCF on a wrong number - and every one of those would need redoing. Verify
> once, then everything built on top inherits that confidence.
>
> **WHERE ELSE** - this is why software teams run tests before deploying, and
> why auditors verify opening balances before examining transactions. Errors
> caught early are cheap; errors caught late are expensive.

**Next session's priority (not today):** build the ratio engine - margins,
ROIC, cash conversion. That's the first genuinely *analytical* thing in this
project, and everything so far has been plumbing. Ratios need no currency
conversion (currency cancels out in a ratio), so nothing blocks it.

---

# PART 0 - Protect your work (5 minutes, do this first)

> **WHAT** - make a copy of your project folder somewhere that isn't your
> laptop.
> **WHY** - right now your project exists in exactly one place. A dead hard
> drive, a mistaken delete, or a "cleaning up Downloads" moment would cost
> you weeks. This takes five minutes and removes that entire category of risk.
> **WHY not GitHub today** - GitHub is the proper answer and we'll set it up
> soon, but a first-time setup (installing Git, authentication, first push)
> can easily take an hour. That's not a good use of today. A simple copy gives
> you 90% of the protection for 5% of the effort.
> **WHERE ELSE** - "3-2-1 backup": three copies, two different media, one
> off-site. Professionals apply this to anything they can't afford to lose.

## 0A. Copy the whole project folder to cloud storage

1. Open File Explorer, go to `C:\Users\User\Downloads`
2. Right-click the `ifrs-pipeline` folder → **Copy**
3. Paste it into OneDrive, Google Drive, or a USB stick - anywhere that isn't
   this laptop's Downloads folder

**YOU SHOULD SEE:** a second copy of `ifrs-pipeline` in your chosen location.

> **NOTE** - the `.venv` folder is large and makes this slow. You can safely
> skip it: it's just installed packages, rebuildable anytime with
> `pip install -r requirements.txt`. The irreplaceable parts are `data`,
> `scripts`, and the `.md` files.

## 0B. Check your password file won't get published later

Type this in PowerShell once it's open (Part B), or open the file directly:

The file `.gitignore` should contain a line saying `.env`.

> **WHAT `.gitignore` does** - lists files that should never be uploaded when
> you eventually put this on GitHub.
> **WHY it matters** - `.env` holds your database password. A password
> committed to a public repository is permanently in that repository's
> history, even if you delete it later. Yours was already exposed once in our
> chat and had to be reset - let's not repeat that in a place recruiters look.
> **EXAMPLE** - this is such a common mistake that GitHub scans public pushes
> for leaked credentials and emails you automatically. Better to never send
> them.

---

# GLOSSARY - words you'll see today

Read this once now. Come back to it whenever a word looks unfamiliar.

**PowerShell** - the black/blue window where you type commands instead of
clicking buttons. Already built into Windows. It's how you control Python.

**Command** - a line of text you type into PowerShell, then press Enter.

**Script** - a file ending in `.py` containing Python instructions. Running a
script means telling Python to follow those instructions.

**Virtual environment (the `.venv` folder)** - a private box holding this
project's own copy of Python and its add-on packages, so it can't clash with
anything else on your computer. You must switch it on each session.

**Package / library** - pre-written code someone else made that you can use.
Arelle (reads XBRL files) and pandas (handles tables of data) are packages.

**Database** - organised storage for your numbers. Yours lives on the internet
at Neon, not on your laptop.

**XBRL** - the tagging system that makes financial reports machine-readable.
Instead of just "43,486,800,000" sitting in a PDF, XBRL says *this number is
Revenue, in euros, for the year 2024*.

**Fact** - one tagged number. Not "Revenue" as an idea - the specific value
€43,486,800,000, tagged as Revenue, for L'Oréal, for 2024.

**Concept / tag** - the label attached to a fact, like `ifrs-full:Revenue`.
Think of it as the name of a row in a financial statement.

**Extension tag** - a concept a company invented for itself because the
standard list didn't have what it needed. You can spot them by the prefix:
`loreal:`, `LVM:`, `essi:`, `shel:`.

**Mapping** - your translation table. It says "when you see the tag
`ifrs-full:Revenue`, that means Revenue, and it belongs on the income
statement."

**Parsing** - reading a file and pulling structured information out of it.

**Taxonomy** - the official dictionary of all possible XBRL concepts.

---

# PART A - Put your downloaded files in the right places

You downloaded several files from the chat. They're all in your **Downloads**
folder. Each needs to go somewhere specific.

> **WHY THIS MATTERS** - Python finds files by their exact location. If a
> script is in the wrong folder, you'll get "file not found" even though the
> file exists. Getting this right now prevents confusing errors later.

Your project folder is:
```
C:\Users\User\Downloads\ifrs-pipeline\ifrs-pipeline
```
Note "ifrs-pipeline" appears **twice**. That's correct - unzipping made a
folder inside a folder.

## A1. Open two File Explorer windows side by side

1. Press **Windows key + E**. A file window opens.
2. Press **Windows key + E** again. A second one opens.
3. Drag one to the far left of the screen until it snaps to half the screen.
   Drag the other to the far right.

- **LEFT window**: click "Downloads" in the sidebar.
- **RIGHT window**: click Downloads → double-click `ifrs-pipeline` →
  double-click `ifrs-pipeline` again.

## A2. Move the 5 Python files into the `scripts` folder

In the RIGHT window, double-click the **scripts** folder to go inside it.

In the LEFT window (Downloads), find these 5 files:
- `_extend_mapping_batch.py`
- `08_validate.py`
- `09_batch_load.py`
- `10_auto_classify.py`
- `07_generate_statements.py`

Click the first one, hold **Ctrl**, click the other four. Press **Ctrl+X**
(cut). Click into the RIGHT window. Press **Ctrl+V** (paste).

Windows will ask about `07_generate_statements.py` because one already exists.
Choose **"Replace the file in the destination."** That's correct - it's an
updated version.

**YOU SHOULD SEE:** the scripts folder now contains files numbered 00 through
10, plus two starting with `_extend_mapping`.

## A3. Move `companies.yaml` into the `data` folder

In the RIGHT window, click the **back arrow** (top-left) to leave scripts.
Now double-click the **data** folder.

⚠️ Put it in `data`, **NOT** in `data\mappings`. You're in the right place if
you can see folders named `mappings` and `raw`.

In the LEFT window, cut `companies.yaml`, then paste into the RIGHT window.

> **WHAT is companies.yaml** - a plain text list matching each downloaded
> filing to a proper company name and its expected currency.
> **WHY** - the file is called `essilorluxottica_new.zip`, but you want the
> company to show up as "EssilorLuxottica" in your database, not that.
> **EXAMPLE** - one entry reads:
> ```
> essity:
>   name: "Essity"
>   expected_currency: SEK
> ```
> The `expected_currency` acts as an alarm: if Essity's filing turns out to
> report in something other than SEK, something is wrong and you want to know.

## A4. Move the documentation files into the main project folder

In the RIGHT window, click the **back arrow** once to return to
`ifrs-pipeline\ifrs-pipeline`.

In the LEFT window, select `NOTES.md`, `LEARNING.md`, and this file
(`TOMORROW.md`). Cut, then paste into the RIGHT window.

**YOU SHOULD SEE:** the folder now has `NOTES.md`, `LEARNING.md`,
`TOMORROW.md`, `README`, `requirements`, `.env`, `.gitignore`, and folders
`scripts`, `data`, `sql`, `.venv`.

---

# PART B - Start PowerShell and switch things on

## B1. Open PowerShell inside the project folder

In the RIGHT window (at `ifrs-pipeline\ifrs-pipeline`), click once on the
**address bar** at the top - the strip showing
`Downloads > ifrs-pipeline > ifrs-pipeline`.

It becomes editable text. Delete it, type:
```
powershell
```
Press **Enter**.

> **WHY this trick** - PowerShell always works "inside" one folder at a time.
> Opening it this way starts it already inside your project, so you don't have
> to navigate there by typing paths.

**YOU SHOULD SEE:** a dark window opens, last line ending in
`ifrs-pipeline\ifrs-pipeline>`.

## B2. Allow scripts to run

Type this, press Enter:
```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

> **WHAT** - Windows blocks scripts by default as a security measure. This
> lifts that block.
> **WHY** - without it, the next step fails with a red "running scripts is
> disabled" error.
> **WHERE ELSE** - the same idea as macOS asking "are you sure you want to
> open this app from the internet?" Operating systems default to distrusting
> scripts.
> **NOTE** - `-Scope Process` means it applies **only to this window**. Close
> PowerShell and you must run it again next time. That's intentional - the
> block goes back up when you're done.

**YOU SHOULD SEE:** nothing at all - just a fresh empty prompt line. Silence
means success. (If it asks a yes/no question, type `Y` and press Enter.)

## B3. Switch on the virtual environment

Type:
```
.venv\Scripts\Activate.ps1
```

**YOU SHOULD SEE:** your prompt now begins with `(.venv)` in green.

If you get a red error, go back and redo B2 - it must succeed first.

## B4. Check your database connection is the real one

Type:
```
type .env
```

> **WHAT** - `.env` is a small file holding your database password and
> address. `type` just prints a file's contents to the screen.
> **WHY check** - during testing we temporarily pointed it at a fake local
> database. If that value is still there, every database command today would
> silently write to nowhere useful.

**YOU SHOULD SEE:** one line starting `DATABASE_URL=postgresql://neondb_owner:`
and containing `neon.tech`.

⚠️ **If you see `localhost` or `testpw` instead - STOP.** That's the leftover
test value. Ask before continuing.

---

# PART C - Apply the full concept mapping

## C1. Run the mapping script

Type:
```
python scripts\_extend_mapping_batch.py
```

> **WHAT this does** - your mapping file currently knows 100 concepts (built
> from L'Oréal alone). This adds 203 more, found across all 11 companies.
> **WHY** - a fact whose tag isn't in the mapping simply won't load. Without
> this, most of LVMH, Essity, and Shell would silently vanish.
> **EXAMPLE** - Shell uses `ifrs-full:RawMaterialsAndConsumablesUsed`.
> L'Oréal never does, so it was missing. Now it's there, labelled "Raw
> Materials and Consumables Used" and assigned to the income statement.

Takes a few seconds.

**YOU SHOULD SEE** something very close to:
```
Already covered (no action needed): 0
Newly added: 203

Updated data/mappings/ifrs_concepts_v0.yaml
  income_statement: 74 concepts total
  balance_sheet: 64 concepts total
  cash_flow: 97 concepts total
  other: 68 concepts total
```
Those four numbers should total roughly **303**.

⚠️ **STOP AND ASK IF:** the total isn't near 303, or you see the word
"MISSING".

---

# PART D - Dry run (safest and most informative step of the day)

## D1. Run it

Type:
```
python scripts\09_batch_load.py --dry-run
```

> **WHAT `--dry-run` means** - do everything except actually save. It parses
> all 11 filings and reports exactly what *would* happen, then stops.
> **WHY** - nothing can break. You see problems before they touch your
> database.
> **WHERE ELSE** - this is a standard idea across serious tools. Terraform
> calls it `plan`, git has `--dry-run`, installers have "preview changes."
> Any tool that makes big changes should offer a way to look first.

**This takes several minutes.** It's parsing 11 large files one by one. Lots
of text will scroll past - that's normal. Wait until your `(.venv) PS ...>`
prompt returns.

**YOU SHOULD SEE** at the very end, a table like:
```
SUMMARY
Company                Status         Parsed   Loaded  Currency
L'Oreal                dry run           572      300  EUR
LVMH                   dry run           569      ...  EUR
...
Essity                 dry run           691      ...  SEK
Shell                  dry run           489      ...  USD
```

## D2. Send me a screenshot of that SUMMARY table

I'm checking for:
- Any company saying "parse failed", "0 facts", or "zip missing"
- Any **CURRENCY MISMATCH** warnings
- How many concepts still aren't mapped

---

# PART E - Load everything into the database for real

## E1. Run the loader

Type this **exactly** - the `--reset-facts` part is important:
```
python scripts\09_batch_load.py --reset-facts
```

> **WHAT `--reset-facts` does** - deletes a company's existing numbers before
> loading them fresh.
> **WHY it's essential here** - L'Oréal is already in your database from your
> last session. Without this flag, loading again would add a **second copy**
> of every L'Oréal number. Your revenue would appear twice, and any total
> would be double the truth - with no error message. I tested this
> specifically; it does happen.
> **WHY it's safe** - for the other 10 companies there's nothing to delete, so
> it does nothing. Safe to use every single time.
> **WHERE ELSE** - this is the difference between an operation that's
> *idempotent* (running it twice gives the same result) and one that isn't.
> It's a core idea in data engineering - worth knowing the word, it comes up
> in interviews.

Several minutes again.

**YOU SHOULD SEE:** a table like the dry run, but with real "Loaded" numbers
and Status showing `OK`.

---

# PART F - Validate (where the real work starts)

## F1. Run the validator and save the output to a file

Type:
```
python scripts\08_validate.py > validation_output.txt
```

> **WHAT the `>` symbol does** - sends the output into a file instead of the
> screen. The file appears in your project folder.
> **WHY** - screenshots cut off long output. A file captures everything and
> you can upload it directly to me.
> **WHERE ELSE** - `>` works in essentially every command line, on every
> operating system. Very worth remembering.
> **NOTE** - your screen will look empty because the text went into the file
> instead. That's correct, not a failure.

To also see it on screen while saving it, use this instead:
```
python scripts\08_validate.py | Tee-Object validation_output.txt
```

## F2. Upload `validation_output.txt` to me

It's in your project folder next to `README`.

> **WHAT this script checks** - three rules of accounting that must be true if
> the mapping is correct:
>   1. Total Assets = Total Equity and Liabilities
>   2. Gross Profit = Revenue − Cost of Sales
>   3. Current Assets + Non-current Assets = Total Assets
>
> **WHY these three** - they're *identities*: true by definition, not by
> coincidence. If one fails, a tag must be mapped to the wrong concept.
> **EXAMPLE** - if `ifrs-full:Assets` were accidentally mapped to "Current
> Assets", then Assets would be smaller than Equity+Liabilities and check 1
> would fail immediately.

**EXPECT FAILURES. That is the entire point of this step.** I classified ~300
concepts by reading tag names without seeing how each company presents them.
Some are certainly wrong. These checks are how we find out which.

It will also warn that multiple currencies are present (Essity=SEK,
Shell=USD). **That warning is correct and expected** - not an error.

---

# PART G - Reality check against the published report (20 min)

**This is the most important verification of the day**, and no script can do
it for you.

> **WHY the automated checks aren't enough** - Part F checks *internal
> consistency*: do the numbers agree with each other? But those checks would
> all pass even if every number were wrong in the same way. If a scaling bug
> divided everything by 1,000, Assets would still equal Equity+Liabilities.
> Consistent and correct are different things.
>
> **WHAT closes that gap** - comparing your database against the actual
> published report, read by a human. Once. Five numbers.
>
> **WHERE ELSE** - this is the difference between *verification* ("did we
> build it right?") and *validation* ("did we build the right thing?"). Both
> are needed. In auditing it's the same idea as vouching a figure back to
> source documentation - internal recalculation is never sufficient on its own.

## G1. Generate L'Oréal's statements

```
python scripts\07_generate_statements.py --company "L'Oreal"
```

This prints the statements and saves an Excel file in `data\raw\`.

## G2. Open L'Oréal's actual published annual report

Go to L'Oréal's investor relations website and find the 2024 or 2025 annual
report PDF - the human-readable one, not the XBRL package.

## G3. Compare exactly five numbers

Check these against your output, year by year:

| Line item | Your database | Published report | Match? |
|---|---|---|---|
| Revenue | | | |
| Gross Profit | | | |
| Operating Profit | | | |
| Total Assets | | | |
| Cash Flow from Operations | | | |

**What you're checking for:**
- **Exact match** → excellent, your pipeline is genuinely correct
- **Off by a factor of 1,000 or 1,000,000** → a scaling problem (the report
  may present in millions while you store full units - not necessarily a bug,
  but you must know which)
- **Wrong sign** → a sign-convention issue (expenses stored positive where
  they should be negative, or vice versa)
- **Completely different number** → a mapping error; that tag points to the
  wrong concept

## G4. Tell me what you found

Even "all five matched perfectly" is valuable information - it means the
foundation is solid and we can build on it with confidence.

---

# PART H - Let the taxonomy check my work (if time)

Only do this after G. It's the most interesting step intellectually, but it's
the lowest priority - it improves FUTURE classification rather than fixing
anything now.

## H1. Run the auto-classifier against LVMH

Type:
```
python scripts\10_auto_classify.py data\raw\LVMH_2024.zip --compare data\mappings\ifrs_concepts_v0.yaml > autoclassify_output.txt
```

> **WHAT this does** - instead of guessing which statement a concept belongs
> to, it reads the answer out of the filing itself, then compares that against
> my hand-classifications.
>
> **WHY this works** - I'd been treating the filing as just a bag of numbers.
> It's much richer than that. Every filing contains a *presentation linkbase*:
> the company's own declaration of its statement structure - which lines go on
> which statement, in what order. It also states, for every concept:
>   - `periodType`: `instant` (a snapshot in time) or `duration` (a flow over
>     a period). An instant value is a balance sheet item by definition.
>   - `balance`: `debit` or `credit` - which tells you the natural sign.
>   - `label()`: the official name, often in several languages.
>
> **WHERE ELSE** - the general lesson is *prefer the authoritative source over
> inference*. I was inferring meaning from tag names when the file already
> declared it. That mistake - reimplementing something your data already
> contains - is extremely common in real data work.
>
> **EXAMPLE** - IFRS numbers its statement sections. A concept appearing in a
> section labelled `[310000] Statement of profit or loss` is an income
> statement item, stated by the filer. No guessing needed.

**YOU SHOULD SEE** in the file: a count of how often the taxonomy agrees with
me, then a list of every disagreement with the official label.

Each classification is tagged with how confident it is:
- `IFRS role [3xxxxx]` - authoritative, from the filing's own structure
- `periodType=instant` - certain by definition
- `concept-name keyword (LOW CONFIDENCE)` - a guess, honestly flagged
- `REVIEW` - couldn't determine; left for a human

**Where the taxonomy and I disagree, the taxonomy is almost certainly right.**
It's the company's own declaration; I was inferring from a name.

## H2. Upload `autoclassify_output.txt` to me

---

# PART I - Put your project on GitHub (45-60 min, ideally its own session)

> **Do this when you have a fresh hour**, not squeezed into the end of a long
> session. It's not hard, but it has several steps and one fiddly bit
> (authentication). Rushing it is how people end up half-finished and
> confused.

> **WHAT GitHub is** - a website that stores copies of code projects. It keeps
> a full history of every change, so you can see what changed, when, and undo
> anything.
>
> **WHY it matters for you, specifically two reasons:**
> 1. **Backup** - your work survives a dead laptop.
> 2. **This is what recruiters look at.** Your roadmap says this project is
>    portfolio material. A GitHub link with a clear README and a real commit
>    history is the artifact. Files on your laptop can't be shown to anyone.
>
> **WHERE ELSE** - Git (the underlying tool) is used by essentially every
> software team in the world, and increasingly by finance and data teams too.
> Knowing basic Git is a genuine line on a CV.
>
> **EXAMPLE of what it gives you** - three weeks from now you change the
> mapping file and the numbers break. With Git you can see exactly what
> changed and revert it in one command. Without it, you're guessing.

## I1. Check whether Git is already installed

In PowerShell, type:
```
git --version
```

**YOU SHOULD SEE:** something like `git version 2.4x.x.windows.1`

**If you get "not recognized"**, Git isn't installed. Go to
https://git-scm.com/download/win, download the 64-bit installer, run it, and
**accept every default** by clicking Next through the whole thing. The
defaults are sensible and one of them (Git Credential Manager) is what makes
step I6 work smoothly.

Then **close PowerShell and reopen it** (Part B steps B1-B3 again), and
re-check `git --version`.

## I2. Create a GitHub account

Go to https://github.com and sign up. Free. Remember your username - you'll
need it shortly.

## I3. Tell Git who you are

Git labels every change with a name and email. Type these two commands,
substituting your own details:

```
git config --global user.name "Your Name"
```
```
git config --global user.email "your.email@example.com"
```

> **WHY** - every commit is permanently stamped with this. Use the same email
> as your GitHub account so your commits link to your profile.
> **NOTE** - `--global` means "for all my projects", so you only do this once
> ever, not per project.

**YOU SHOULD SEE:** nothing. Silence means success.

## I4. ⚠️ CRITICAL - verify your password file is protected

**Do this before anything gets uploaded.** Type:
```
type .gitignore
```

**YOU SHOULD SEE** a list including a line that says exactly:
```
.env
```

**If `.env` is NOT in that list, stop and tell me.** Uploading it would put
your database password on the internet permanently - deleting it later does
not remove it from the history.

> **WHY this is so serious** - Git's whole purpose is remembering everything
> forever. A password committed once stays in the repository's history even
> after you delete the file. The only real fix is rotating the password.
> GitHub actually scans public pushes for leaked credentials and emails you -
> that's how common this mistake is.

## I5. Turn your folder into a Git repository

Make sure PowerShell is in your project folder (prompt ends with
`ifrs-pipeline\ifrs-pipeline>`), then:

```
git init
```
> **WHAT** - creates a hidden `.git` folder that tracks changes. Your files
> are untouched.

```
git add .
```
> **WHAT** - stages everything for the first snapshot. The `.` means "this
> folder and everything in it" - except whatever `.gitignore` excludes.

**Now verify `.env` was excluded.** Type:
```
git status
```

**YOU SHOULD SEE** a long green list of files - and **`.env` must NOT appear
in it.** Scan for it specifically.

⚠️ **If `.env` IS listed**, stop. Run `git reset` to unstage everything, and
tell me before continuing.

> **WHAT ELSE your `.gitignore` excludes, and why it's correct:**
>
> | Excluded | Why |
> |---|---|
> | `.env` | Contains your database password |
> | `.venv/` | Thousands of installed package files, all rebuildable with `pip install -r requirements.txt` |
> | `data/raw/*` | Your filing zips - roughly 350MB total, and Shell alone is 55MB |
> | `__pycache__/`, `*.pyc` | Temporary files Python generates automatically |
>
> **WHY excluding `data/raw` is right** - GitHub is for *code*, not bulk data.
> It rejects files over 100MB and slows badly on large repos. Your zips are
> re-downloadable anytime with `00_find_filing.py`, so nothing is lost.
>
> **BUT this means GitHub is not a complete backup.** Your filings and
> generated outputs stay local only. That's exactly why Part 0 (copying the
> whole folder to cloud storage) is still worth doing - the two protect
> different things.
>
> **What IS backed up and matters most:** all your scripts, the SQL schema,
> and `data/mappings/` - which holds the 303 hand-verified concept
> classifications. That mapping file is the most labour-intensive artifact in
> the project, and losing it would genuinely hurt.

```
git commit -m "Initial commit: IFRS/XBRL pipeline V0 complete"
```
> **WHAT** - saves the snapshot with a message describing it.
> **WHY messages matter** - in six months, "Initial commit: IFRS/XBRL pipeline
> V0 complete" tells you something. "stuff" tells you nothing. Write them for
> your future self.

**YOU SHOULD SEE:** a summary like `XX files changed, XXXX insertions(+)`.

## I6. Create the repository on GitHub and connect it

1. Go to https://github.com and click the **+** in the top right → **New repository**
2. **Repository name**: `ifrs-pipeline`
3. **Description**: `Automated IFRS/XBRL financial statement pipeline`
4. Choose **Private** for now
5. ⚠️ **Do NOT tick** "Add a README file", "Add .gitignore", or "Choose a
   license" - you already have these locally, and adding them here creates a
   conflict that's annoying to resolve as a beginner
6. Click **Create repository**

> **WHY private first** - your README is still the generic starter version.
> Make it public once it actually represents your work well. Switching from
> private to public later is one click in Settings.

GitHub now shows a page with commands. Use the two under **"…or push an
existing repository from the command line"**. They look like this, with your
username substituted:

```
git remote add origin https://github.com/YOUR-USERNAME/ifrs-pipeline.git
```
> **WHAT** - tells your local Git where the online copy lives. "origin" is
> just the conventional nickname for it.

```
git branch -M main
```
> **WHAT** - names your main line of work `main`, matching GitHub's default.

```
git push -u origin main
```
> **WHAT** - uploads everything.

**A browser window will pop up asking you to sign in to GitHub.** That's Git
Credential Manager (installed with Git) handling authentication. Sign in and
approve.

> **WHY a browser and not a password** - GitHub stopped accepting passwords
> over the command line in 2021 for security reasons. Browser-based sign-in
> replaced it and is more secure.

**YOU SHOULD SEE:** upload progress, ending with something like
`branch 'main' set up to track 'origin/main'`.

## I7. Confirm it worked

Refresh your repository page on GitHub. You should see all your folders and
files listed - and **no `.env` file**. Check that specifically.

## I8. From now on - the three-command rhythm

After any work session:
```
git add .
git commit -m "describe what you changed"
git push
```

> **WHY commit regularly** - each commit is a restore point. Commit after each
> meaningful chunk of work rather than once a week, and the history becomes
> genuinely useful rather than one giant blob.
> **EXAMPLE messages** - "Fix balance sheet year off-by-one", "Add 203
> concepts from batch classification", "Load 11 companies into database".

---

# WHAT TO SEND ME (in order)

1. Screenshot of the **Part C** output (the 303 concept count)
2. Screenshot of the **Part D** SUMMARY table
3. Screenshot of the **Part E** SUMMARY table
4. Upload `validation_output.txt` (Part F)
5. Your five-number comparison from **Part G** - even if all matched
6. Upload `autoclassify_output.txt` (Part H, if you got there)

---

# REALISTIC PLAN FOR THE DAY

| Part | Time | Tier |
|---|---|---|
| 0 - Backup | 5 min | MUST |
| A - Move files | 15 min | MUST |
| B - Start PowerShell | 5 min | MUST |
| C - Apply mapping | 2 min | MUST |
| D - Dry run | 10 min (mostly waiting) | MUST |
| E - Load for real | 10 min (mostly waiting) | MUST |
| F - Validate | 5 min to run, longer to discuss | SHOULD |
| G - Reality check | 20 min | SHOULD |
| H - Auto-classify | 5 min | IF TIME |
| I - GitHub | 45-60 min | **SEPARATE SESSION** |

**Safe stopping points:**
- After **Part D** - nothing written to the database, completely safe
- After **Part E** - all data loaded, database in a clean state
- After **Part G** - foundation verified, ready to build analysis on

Don't rush F and G. Understanding what they tell you matters more than
ticking off H.

**Part I deserves its own fresh session.** It's genuinely valuable - it's your
backup *and* the thing recruiters actually look at - but it has several steps
and one fiddly authentication bit. Attempting it tired, at the end of a long
day, is how people end up half-configured and frustrated.

---

# THE BIGGER PICTURE (worth knowing where this is going)

**What you've built so far is all plumbing** - parsing, mapping, loading,
validating. That's necessary and it's genuinely the hard part, but none of it
is *analysis* yet. You haven't computed a single margin.

That's the right order - you can't analyse data you haven't verified - but
it's worth naming, because the actual goal of this project is financial
analysis, not data engineering. The risk to watch for is building beautiful
infrastructure and never producing an insight about L'Oréal versus LVMH.

**So: next session is the ratio engine.** Margins, ROIC, cash conversion,
across all 11 companies. It's the first thing that produces an *answer* rather
than a pipeline, and it needs no currency conversion because ratios are
currency-neutral.

**One ongoing thing to take seriously:** most of this code was written for
you. If you can't explain or modify it, it isn't really your project - and an
interview will establish that quickly. "How does your parser handle taxonomy
resolution?" is a completely fair question about work you claim as yours.

The fix isn't to read more. Pick one small script - `06_check_revenue.py` is
about fifteen lines - and rewrite it yourself from scratch, without copying.
It'll be slower and uglier. That's fine. **Code you understand beats better
code you don't**, every time, especially when something breaks at 11pm or an
interviewer asks a follow-up question.

---

# IF SOMETHING GOES WRONG

Send me:
1. Which step number (e.g. "C1", "E1")
2. A screenshot of the **whole** PowerShell window, not just the error line

Common issues:

| Symptom | Likely cause |
|---|---|
| Red "running scripts is disabled" | B2 didn't run - redo it |
| No `(.venv)` in prompt | B3 didn't run, or you opened a new window |
| "File not found" | file is in the wrong folder - recheck Part A |
| "DATABASE_URL not found" | you're not in the project folder, or `.env` is missing |
| Command seems frozen | parsing is slow - wait 5 minutes before worrying |

---

# WHEN YOU WANT TO CHECK YOUR UNDERSTANDING

Say **"let's revise"** or **"sum up"** and I'll ask you questions rather than
just summarising. Answering from memory is what makes things stick -
re-reading feels productive but doesn't build recall nearly as well.

Getting answers wrong is the point. That's how you find the gaps.
