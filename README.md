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
| `conflicts()` | Lists places where pages inside the same snapshot publish different values for the same product, tier and measure, with the value the system treats as current and why. One entry per product, measure and tier; a disagreement seen again in a later snapshot is reported once, from that snapshot. |

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
  answered from the last snapshot with a sentence saying so. Inside a snapshot, a statement
  marked *effective <date>* (MM/DD/YYYY, "Month D, YYYY" or YYYY-MM-DD; the line just below a
  table or just above it, a rate sentence, the line above a branch schedule, a contact-page
  line, a paragraph) is in force only from that date: a value published in January to take
  effect in March is not the January answer. An in-force value wins and the scheduled one is
  mentioned in `conflict_note`; if only a future-dated value exists, the answer describes it as
  not yet in effect (`confident=False`) instead of asserting it. The date binds only the
  statement it sits next to, never a neighbouring table or section.
* **Removed and withdrawn pages.** A page that disappears from later snapshots can still
  answer a question, flagged as last seen in an earlier snapshot: a missing page is treated as
  ambiguous, not as a withdrawal. A withdrawal is a notice in the selected snapshot that is
  affirmative ("has not been discontinued" is not one), in force on the reference day ("will be
  discontinued effective <date>" applies from that date) and about the product asked for (the
  notice's subject or object carries the question's product words, or the family word for a
  family-wide notice, and names no other product or variant). Such a notice binds values from
  removed pages and values still published alike: the answer reports the notice with
  `confident=False` and keeps the published value in `conflict_note` only. Values that
  *change* on a page that still exists are never taken from the older version.
* **Qualifiers and identity.** Term, balance tier, model year, personal vs. business, regular
  vs. jumbo, standard vs. introductory and IRA products are matched separately. Product identity
  comes from the table's own context (headings and the short label lines preceding it). A
  question that names a product the site does not have is declined rather than answered with a
  similar product.
* **Sentences vs. tables.** A percentage in a sentence becomes a rate fact only when the sentence
  states exactly one percentage, names the measure (rate, APR, APY, dividend rate) and sits under
  a heading that names the product. Percentages that describe a loan type ("fixed-rate"), an
  increment or cap ("add 1%", "adjust up to 2%"), a share ("20% of the price"), a down payment,
  or a rate that is not a price (a tax rate) are not rates. A sentence is weaker evidence than a
  table about the same product.
* **One resolution for rate facts.** Ordinary rate answers, yes/no questions about a rate and
  `conflicts()` share the same rows, the same grouping of rows that state one fact and the same
  authority order: rate sheet over product pages over promotional, landing and article pages;
  tables over sentences; newer effective dates and fetch dates win ties. Answers and yes/no
  checks use only rows in force and not expired; `conflicts()` also lists the value of an
  expired offer or a product page that still disagrees with the rate sheet, as the losing side,
  so that the disagreement is explained rather than hidden. When pages disagree the answer's
  `conflict_note` says which page was preferred and why. Branch hours, contact numbers and
  paragraphs are not rate rows: they apply the valid-time rule above with rules of their own
  (described below), not the rate-sheet authority order.
* **Declining and confidence.** `confident=False` means the system cannot give the value
  asked for. Plain declines carry no sources and no numbers beyond those in the question. Some
  low-confidence answers do carry sources and extra text: a scheduled future value, a
  discontinuation notice, or the site's contact details for a procedural question the pages do
  not answer. Nothing is invented: with no pages ingested every question is declined.
* **Descriptive questions** (eligibility, programmes, closures, recognitions, fees) are answered
  with the best-matching paragraph and its heading, preferring the organisation's own product
  pages over articles and, among dated articles, the most recent one. A question that names a
  product ("Free Checking") prefers paragraphs about that product; paragraphs about another
  product of the same family, or about business products when the question is not about
  business, are weaker matches. A paragraph that ends by introducing a list is completed with
  the next paragraph under the same heading when that paragraph is a list or carries no
  amounts; a new section is never appended. A fee or minimum question is answered only by a
  paragraph that states an amount or says there is none ("no monthly fee"); mentioning a fee,
  or the word "free" on its own, is not enough. When such a question names a product, the
  paragraph must be about that product (named in its heading, its label line or its text, and
  not a business variant); otherwise the question is declined rather than answered from a
  sibling product. An eligibility question whose best match states no criteria (who may join,
  where one must live or work) is answered with `confident=False`. When the criteria refer to
  an area by name ("within our charter area") the answer is completed, in this order, with a
  sentence that defines the area on the same page or the membership pages, with a definition
  of the charter area or field of membership elsewhere on the site (a full list of places, or a
  list introduced with a colon), or, marked as separate context, with the places served stated
  on the membership pages; each is cited as a further source. Office locations are never
  presented as the eligibility area, and places served by another line of business (an
  insurance agency, a partner) are never used.
* **Branch hours** are bound to the branch and service (lobby / drive-up) they were published
  for; a branch without published hours is declined rather than given another branch's hours.
* **Reaching the organisation.** "How do I contact you?" and "what is your phone number?" are
  answered with the number the site's own contact page presents as its line (phone, call us,
  member service) together with that page's address. A fax, partner, rewards, PIN or fraud
  line is never used for it, and a number appearing often on the page is not evidence; without
  a general number the answer points to the contact page and states no number. A branch's
  phone number is given only when it is listed in that branch's own section; otherwise the
  question is declined without any number. A question about a specific line (a lost card, a
  PIN) is answered from the page text like any other descriptive question.

See `NOTES.md` for the reasoning behind these rules and their known limits.

## Sources and quotes

Each `Source` names the page URL, the snapshot in which that version of the page was observed
and a `quote`. Quotes are excerpts of the page text after Markdown links, emphasis and
footnote markers have been stripped and whitespace collapsed (a table row is quoted as the row
itself). The tests check every quote against the cleaned text of the cited page version. A
quote shows what was matched; whether it answers the question is for the reader to judge, and
the low-confidence cases above are where the system itself is unsure.

Two parsing boundaries matter when reading a quote. A table row's product is read from the
headings and the short label lines above the table, so the quoted row may not repeat the
product name; the answer text does. Dates are read only from the wording *effective <date>*
next to the statement; other dates on a page (events, publication dates) do not change when a
value applies.

## Running an external evaluation

The harness, scorer, question sets and website snapshots used to evaluate this system are
kept outside this repository. With such a kit unpacked at `$KIT`, the usual invocation is:

```bash
python $KIT/harness/run_eval.py --submission /path/to/this/repo \
    --snapshots $KIT/snapshots --eval $KIT/eval/public_eval.jsonl --out /tmp/results
```

On that kit's public set of 40 questions the current code scores 0.96 on the harness's QA
measure with zero stale-value trap hits and zero errors; all five sample questions and the
sample conflict of the kit's self-test pass. P95 answer latency is about 0.2 s and ingesting
five snapshots (about 3,300 page versions) takes about 4–5 s on a laptop. The public figure
covers question answering only: the kit publishes no reference list of conflicts for that
data, so the conflicts part of the harness's score is not measured, and the system's own
conflict list for that data has not been checked against a reference. No accuracy is claimed
for it. Those figures come from running the harness locally and depend on the private data;
they cannot be reproduced from this repository alone.

## Quick demo on the fictional fixtures

The following runs from the repository root with no dependencies and prints a few answers
and the conflict list for the fixture snapshots:

```bash
python3 - <<'EOF'
import json, sys
from pathlib import Path
from types import SimpleNamespace as N
sys.path.insert(0, ".")
import solution
ks = solution.build()
for folder in sorted(Path("tests/fixtures/snapshots").iterdir()):
    manifest = json.loads((folder / "manifest.json").read_text())
    pages = [N(url=p["url"], title=p.get("title", ""), markdown=(folder / p["file"]).read_text(),
               fetched_at=p.get("fetched_at", manifest["taken_on"])) for p in manifest["pages"]]
    ks.ingest(N(snapshot_id=manifest["snapshot_id"], taken_on=manifest["taken_on"], pages=pages))
for q, day in [("What is the 6-month certificate APY?", None), ("What is the 6-month certificate APY?", "2026-02-01"),
               ("What are the lobby hours for the Harbor branch?", None), ("How do I contact you?", None),
               ("Who is eligible to become a member?", None)]:
    a = ks.answer(q, as_of=day)
    print(f"{q} [{day or 'latest'}] -> confident={a.confident}\n  {a.text}\n  sources: {[s.url for s in a.sources]}")
for c in ks.conflicts():
    print(f"conflict: {c.topic}: {c.values} -> {c.resolution}")
EOF
```

Expected output (abridged): `Regular Certificates, 6 Month: 4.10% APY.` for the latest snapshot
and `4.00% APY` as of 2026-02-01; the Harbor branch lobby schedule; the contact page's number;
the membership criteria; and three conflicts, the first being
`Regular Certificates, 6-month APY: ['4.10% APY', '4.25% APY'] -> 4.10% APY`.

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
* Valid time is read only from the wording *effective <date>*. A change announced in other
  words ("starting in March", "from 1 March") is not recognised and the value is treated as
  in force. A withdrawal is recognised only from a short list of predicates (discontinued, no
  longer offered, retired, closed to new accounts...) in a sentence about the product; a very
  short notice (fewer than three content words) is not indexed and so is not seen.
* A product named only by a bare label line above its paragraph is recognised for fee and
  minimum questions, but such label lines are not part of the retrieval index, so a question
  about that product may still rank another paragraph higher.
* Branch schedules are labelled with the page's publication date when the page carries one;
  a page whose first date is an effective date shows that date instead.
* Conflict resolution follows authority rules (rate sheet > product page > other; newer
  effective date; expired offers lose). It does not know which of two disagreeing official
  pages is a typo, and its output has not been validated against a reference list.
* Only percentage measures (rate, APR, APY, dividend rate) are parsed as structured facts;
  fees, minimums and other amounts are answered from text, by the paragraph that states the
  amount or says there is none.
* A procedural question the pages do not answer ("how do I update my address?") is answered
  with the site's own contact channel, including its phone number, as a low-confidence answer.
  This is a deliberate choice: the number is the useful part of the answer, even though a
  grader that treats any digit in a declined answer as an invented value would count it against
  the system.
* Detecting that a question names an unknown product relies on the word's position and
  capitalisation; an unusual but legitimate wording can be declined.
