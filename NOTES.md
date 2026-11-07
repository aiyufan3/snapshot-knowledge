# Notes

## What counts as "current"

The newest ingested snapshot is the current state of the website; the machine clock plays no
role. `as_of` selects the newest snapshot taken on or before that date, which treats the gap
between two snapshots as "the site as last observed". A date earlier than the first snapshot is
declined because nothing was observed then. A date later than the last snapshot is answered
from the last snapshot and the answer says that it reflects the most recent snapshot.

## Observation time vs. valid time

Seeing a value in a snapshot is not the same as the value being in force. A table's
`Rates are effective as of …` line (only that wording; event dates in prose are ignored) gives
each row a valid-from date. A row is in force on the reference day — `as_of`, or the snapshot
date when `as_of` is absent — when it has no effective date or its date is on or before that
day. Rows whose date is still ahead are *scheduled*: they are left out of answers, yes/no
checks and conflicts, mentioned in the answer's `conflict_note` when an in-force value exists,
and described as "not yet in effect" with `confident=False` when nothing in force exists.
Expiry wording is honoured only when it refers to the offer or rate itself (for example
"offer ended 2025-12-31"); conditional footnotes do not expire a table.

## History and removed pages

Every snapshot is indexed separately, so historical questions read the snapshot that applied.
Pages that exist in an earlier snapshot but not in the selected one are kept as a "last seen"
set. They are consulted only when the selected snapshot does not answer the question (or
answers it less completely). Before such an answer is used, the selected snapshot is searched
for a sentence that names the same product and says it was discontinued, retired, withdrawn or
is no longer offered. If one exists the answer reports that status with `confident=False`,
cites the notice, and keeps the last published value in `conflict_note` so that the current
status and the historical value are never mixed. Otherwise the last-seen answer is given with
a note saying the page is no longer on the site. A value that changed on a page that still
exists is never taken from the older version. The assumption: a disappearing page is
ambiguous (removed or not re-crawled) unless the site says otherwise, while a changed page is
an explicit update.

## One truth for every path

Ordinary rate answers, yes/no rate questions and `conflicts()` reuse the same pieces: the
applicability test (in force on the reference day, not expired, not a weak prose statement
superseded by a table), the same grouping of rows that state the same fact, and the same
authority order (rate-sheet pages over product pages over promotional, landing, calculator and
article pages; tables over sentences; newer effective date; newer fetch date). A yes/no
question is answered from the resolved value of each fact, so it cannot say "yes" to a value
that the ordinary answer rejects.

## Product identity

Rows are compared only when they describe the same product. Identity is the product family
plus the distinguishing name words learned from the table's own context (headings and the
short label lines above it), the term, the flags (jumbo, special, IRA, business, introductory)
and compatible tiers (balance or model-year ranges that overlap). Two tables must agree on the
name words exactly — "Alpha" and "Beta" certificates with the same term are different products
— while a sentence may carry extra words ("Spring special: 6-month certificate") and is
compared with a table whose name words it contains. A question that names a product absent
from the site (a capitalised word, or a word that appears nowhere in the snapshot, in the
position of a product name) is declined instead of being answered with a similar product.

## Resolving conflicts

Within one snapshot, comparable in-force rows that disagree form a conflict; the preferred
value follows the authority order above and the `reason` names both pages and what they are.
Normal changes between snapshots are history, not contradictions; a future-dated table is a
scheduled change, not a contradiction. Each distinct conflict is reported once, using its most
recent occurrence. Counts stated as current facts in dated articles (the number of branches)
are also compared; the most recently published figure is treated as current.

## Confidence

`confident=False` means the system cannot give the value asked for. Plain declines contain no
sources and no numbers beyond those in the question. Three kinds of low-confidence answers do
carry sources and extra text: a scheduled value that is not yet in effect, a discontinuation
notice, and the site's own contact details for a procedural question the pages do not answer.
An eligibility question whose best-matching paragraph states no criteria is also returned with
`confident=False`.

## Key assumptions

* Product names often appear as short bare lines above a table rather than as Markdown
  headings; the two most recent such lines, plus the nearest heading when it names a product,
  form the product identity of the table's rows. In-page anchor links written as list items are
  treated as section tabs.
* Prose rate statements are weaker evidence than tables. From promotional or article pages
  they are ignored for answers and conflicts; from product pages they are used only when no
  table covers the same product.
* "How many …" questions are answered from the most recently published dated statement; with
  an explicit or relative year, only statements about that year are used.

## Runtime dependencies and API keys

None. The implementation uses the Python 3.11 standard library only, makes no network calls
and needs no API keys. The evaluation harness this system is normally run with has an optional
LLM-based judge that would require a key; this system does not use it.
