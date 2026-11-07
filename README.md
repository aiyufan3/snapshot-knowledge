# Snapshot Knowledge

A small, dependency-free knowledge system that answers questions about an organisation's
website from a series of dated **snapshots** of that site. It answers as the site stood on a
given date, cites the pages that support each statement, declines when the snapshots do not
support an answer, and reports the places where pages contradict each other.

The website data and the evaluation harness it is normally run with are maintained
separately and are **not** part of this repository. The code is written against the snapshot
format described below rather than against any particular organisation; it was developed and
tested on English-language financial-institution pages (rates, fees, branches, membership) and
has not been tried on other kinds of sites.

## Requirements

* Python 3.11 (standard library only; see `requirements.txt`)
* No network access, API keys or external services at run time

```bash
python3.11 -m venv .venv && . .venv/bin/activate
python -m unittest discover -s tests          # behaviour tests on fictional data
```

## Input format

A snapshot is a folder with a `manifest.json` and the pages as Markdown files:

```
snapshots/
  01_2026-01-10_baseline/
    manifest.json
    pages/p00.md ...
  02_2026-03-15_recrawl/
    ...
```

```json
{"snapshot_id": "01_2026-01-10_baseline", "taken_on": "2026-01-10",
 "pages": [{"file": "pages/p00.md", "url": "https://www.example-cu.example/rates",
            "title": "Rates", "fetched_at": "2026-01-10T08:00:00Z"}]}
```

`tests/fixtures/snapshots/` contains a complete fictional example (an invented credit union,
`northwind-cu.example`) that the tests run against.

## Interface

`solution.py` exposes `build()`, which returns an object with three methods:

| Method | Behaviour |
|---|---|
| `ingest(snapshot)` | Called once per snapshot, oldest first. Parses rate tables and single-line rate statements into rows that keep their product context, tier, term, model-year range and effective date; indexes paragraphs for text retrieval; records links. |
| `answer(question, as_of=None)` | Answers from the newest snapshot, or from the newest snapshot taken on or before `as_of`. Returns `text`, `sources` (url, snapshot id, quote), `confident` and an optional `conflict_note`. |
| `conflicts()` | Lists places where pages inside the same snapshot publish different in-force values for the same product, tier and measure, with the value the system treats as current and why. |

The method signatures and the `Answer` / `Source` / `Conflict` shapes follow a small contract
module when one is importable (an external harness can put it on `sys.path`); otherwise
equivalent stand-in dataclasses are used, so the module also runs on its own.

```python
import solution
ks = solution.build()
for snap in snapshots:            # objects with snapshot_id, taken_on, pages[url,title,markdown,fetched_at]
    ks.ingest(snap)
a = ks.answer("What is the 6-month certificate APY?")
a = ks.answer("What is the 6-month certificate APY?", as_of="2026-02-01")
for c in ks.conflicts():
    print(c.topic, c.values, c.resolution)
```

Example answers from the fictional fixture data:

* *What is the 6-month certificate APY?* → `Regular Certificates, 6 Month: 4.10% APY.` with the rate-sheet page as source.
* *… as_of 2026-02-01* → `… 4.00% APY.` from the earlier snapshot only.
* *… as_of 2025-06-01* (before the first snapshot) → declined: the snapshots do not cover that date.
* *What is your rate on a 7/1 adjustable-rate mortgage?* → declined (`confident=False`); nothing on the site lists it.
* *What are the lobby hours for the Harbor branch?* → the lobby schedule published for that branch, not the drive-up schedule and not another branch's.
* `conflicts()` → `Regular Certificates, 6-month APY: ['4.10% APY', '4.25% APY'] → 4.10% APY`, citing the rate sheet and the promotional page whose offer has ended.

## How answers are produced

* **Observation time vs. valid time.** "Current" means the newest ingested snapshot, never the
  clock of the machine. With `as_of`, only the newest snapshot taken on or before that date is
  used; a date before the first snapshot is declined; a date after the last snapshot is
  answered from the last snapshot with a sentence saying so. Inside a snapshot, a table that
  says *effective <date>* is in force only from that date: a value published in January to take
  effect in March is not the January answer. If only a future-dated value exists, the answer
  describes it as not yet in effect (`confident=False`) instead of asserting it.
* **Removed pages.** A page that disappears from later snapshots can still answer a question,
  flagged as last seen in an earlier snapshot — unless the selected snapshot states that the
  product was discontinued or is no longer offered, in which case the answer reports that and
  keeps the last published value in `conflict_note` only. Values that *change* on a page that
  still exists are never taken from the older version.
* **Qualifiers and identity.** Term, balance tier, model year, personal vs. business, regular
  vs. jumbo, standard vs. introductory and IRA products are matched separately. Product identity
  comes from the table's own context (headings and the short label lines preceding it). A
  question that names a product the site does not have is declined rather than answered with a
  similar product.
* **One resolution for every path.** Ordinary rate answers, yes/no questions about a rate and
  `conflicts()` share the same applicability test (in force, not expired) and the same
  authority order: rate sheet over product pages over promotional, landing and article pages;
  tables over sentences; newer effective dates and fetch dates win ties. When pages disagree
  the answer's `conflict_note` says which page was preferred and why.
* **Declining and confidence.** `confident=False` means the system cannot give the value
  asked for. Plain declines carry no sources and no numbers beyond those in the question. Some
  low-confidence answers do carry sources and extra text: a scheduled future value, a
  discontinuation notice, or the site's contact details for a procedural question the pages do
  not answer. Nothing is invented: with no pages ingested every question is declined.
* **Descriptive questions** (eligibility, programmes, closures, recognitions) are answered with
  the best-matching paragraph and its heading, preferring the organisation's own product pages
  over articles and, among dated articles, the most recent one. An eligibility question whose
  best match states no criteria (who may join, where one must live or work) is answered with
  `confident=False`.
* **Branch hours** are bound to the branch and service (lobby / drive-up) they were published
  for; a branch without published hours is declined rather than given another branch's hours.

See `NOTES.md` for the reasoning behind these rules and their known limits.

## Sources and quotes

Each `Source` names the page URL, the snapshot in which that version of the page was observed
and a `quote`. Quotes are excerpts of the page text after Markdown links, emphasis and
footnote markers have been stripped and whitespace collapsed (a table row is quoted as the row
itself). The tests check every quote against the cleaned text of the cited page version. A
quote shows what was matched; whether it answers the question is for the reader to judge, and
the low-confidence cases above are where the system itself is unsure.

## Running an external evaluation

The harness, scorer, question sets and website snapshots used to evaluate this system are
kept outside this repository. With such a kit unpacked at `$KIT`, the usual invocation is:

```bash
python $KIT/harness/run_eval.py --submission /path/to/this/repo \
    --snapshots $KIT/snapshots --eval $KIT/eval/public_eval.jsonl --out /tmp/results
```

On that kit's public set of 40 questions the current code scores 0.93 on the harness's QA
measure with zero stale-value trap hits and zero errors; all five sample questions and the
sample conflict of the kit's self-test pass. P95 answer latency is about 0.2 s and ingesting
five snapshots (about 3,300 page versions) takes about 4–5 s on a laptop. Those figures come
from running the harness locally and depend on the private data; they cannot be reproduced
from this repository alone, and the system's own conflict list for that data has not been
checked against a reference, so no accuracy is claimed for it.

## Tests and release checks

```bash
python -m unittest discover -s tests            # behaviour tests on fictional data
python tools/privacy_check.py --files README.md # release check (needs a private policy file)
sh tools/install_hooks.sh                       # enable the repository's Git hooks
```

`tools/privacy_check.py` scans the Git index (`--staged`), working-tree files (`--files`) or
the whole history (`--history`) for material that must not be published: project-specific
terms from a policy file kept **outside** the repository, plus built-in rules for keys and
tokens, private-key blocks, home-directory paths, e-mail addresses and attribution trailers.
File paths are checked as well as contents, and every published path must match
`tools/release_allowlist.txt`. In history mode the commit message is checked like file
content; author and committer fields are checked against the policy terms only. Without a
policy file the check fails closed; the functional tests do not need it. The `pre-commit`
hook runs the staged check and the tests; the `pre-push` hook refuses to push unless the
clone was explicitly authorised (`git config release.pushAuthorized yes`) and the full history
passes. The hooks guard against mistakes; they are not a substitute for review.

## Known limitations

* Retrieval is lexical (BM25 over paragraphs with light stemming and a handful of generic
  synonyms). A question phrased very differently from the page can pick a related but wrong
  paragraph; when that happens the cited quote shows exactly what was matched.
* "Last year" and similar relative dates are resolved against the snapshot date, not the
  wall clock. If no figure exists for the requested year the answer says so and reports the
  most recent published figures instead.
* Conflict resolution follows authority rules (rate sheet > product page > other; newer
  effective date; expired offers lose). It does not know which of two disagreeing official
  pages is a typo, and its output has not been validated against a reference list.
* Only percentage measures (rate, APR, APY, dividend rate) are parsed as structured facts;
  fees and other amounts are answered from text.
* Detecting that a question names an unknown product relies on the word's position and
  capitalisation; an unusual but legitimate wording can be declined.
