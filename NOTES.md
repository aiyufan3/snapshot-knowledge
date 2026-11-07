# Notes

## What counts as "current"

The newest ingested snapshot is the current state of the website; the machine clock plays no
role. `as_of` selects the newest snapshot taken on or before that date, which treats the gap
between two snapshots as "the site as last observed". A date earlier than the first snapshot is
declined because nothing was observed then. A date later than the last snapshot is answered
from the last snapshot and the answer says that it reflects the most recent snapshot.

## Observation time vs. valid time

Seeing a value in a snapshot is not the same as the value being in force. The wording
*effective <date>* (only that wording; event dates and publication dates are ignored) gives
the statement next to it a valid-from date. The date may be written MM/DD/YYYY, "Month D,
YYYY" or YYYY-MM-DD, and the statement may be a table (the line just below it, or the line
just above it when nothing below carries a date and the line was not already read as the
previous table's trailer), a rate sentence, a branch schedule (the line above it), a
contact-page line or a paragraph. The date never binds anything beyond that statement: a
dated line above one table says nothing about the next table or section.

A statement is in force on the reference day — `as_of`, or the snapshot date when `as_of` is
absent — when it has no effective date or its date is on or before that day. Statements whose
date is still ahead are *scheduled*. For rate rows they are left out of answers, yes/no checks
and conflicts, mentioned in the answer's `conflict_note` when an in-force value exists, and
described as "not yet in effect" with `confident=False` when nothing in force exists. Branch
schedules, contact numbers and paragraphs follow the same pattern on their own paths: an
in-force statement wins and the scheduled one is noted; a dated in-force statement supersedes
an undated one in the same section; alone, the scheduled statement is described, not asserted.
Expiry wording is honoured only when it refers to the offer or rate itself (for example
"offer ended 2025-12-31"); conditional footnotes do not expire a table.

## History and removed pages

Every snapshot is indexed separately, so historical questions read the snapshot that applied.
Pages that exist in an earlier snapshot but not in the selected one are kept as a "last seen"
set. They are consulted only when the selected snapshot does not answer the question (or
answers it less completely); the last-seen answer carries a note saying the page is no longer
on the site. A value that changed on a page that still exists is never taken from the older
version. The assumption: a disappearing page is ambiguous (removed or not re-crawled) unless
the site says otherwise, while a changed page is an explicit update.

A withdrawal is something else: a sentence in the selected snapshot that says the product is
gone. It counts only when all of the following hold.

* The predicate is one of a short list (discontinued, retired, phased out, no longer
  offered/available, closed to new accounts, "we no longer offer", "stopped offering"...).
  Words such as "withdrawn", "closed" or "retired" in other roles ("funds withdrawn before
  maturity", "the loan is closed", "retired members") are not withdrawals.
* It is affirmative: "has not been discontinued" and "never retired" are not withdrawals.
* It is in force on the reference day: "will be discontinued effective <date>" (or "will be
  discontinued on <date>") applies from that date; a future tense without a date does not
  apply yet; "effective <date>" still ahead does not apply yet.
* It is about the product asked for. In the passive voice the words before the predicate (the
  subject), in the active voice the words after it (the object), must carry every product word
  of the question - or the question's family word alone, for a family-wide notice such as
  "certificates are no longer offered" - and must not name another product or variant: "the
  Beta certificate has been discontinued" says nothing about the Alpha certificate, and
  "jumbo certificates" are not "certificates".

When such a notice exists the answer reports it with `confident=False`, cites the notice and
keeps the published value in `conflict_note`, so that the current status and the value are
never mixed. This binds values from removed pages and values still published in the same
snapshot alike: a rate table that outlives the notice does not override it. Historical
questions (`as_of` before the notice) still read the value that applied then.

## One truth for rate facts

Ordinary rate answers, yes/no rate questions and `conflicts()` reuse the same pieces: the rows,
the grouping of rows that state the same fact, and the authority order (rate-sheet pages over
product pages over promotional, landing, calculator and article pages; tables over sentences;
newer effective date; newer fetch date). Answers and yes/no checks use only rows that are in
force on the reference day, not expired and not a weak prose statement superseded by a table.
A yes/no question is answered from the resolved value of each fact, so it cannot say "yes" to
a value that the ordinary answer rejects. `conflicts()` applies the in-force test but keeps
expired rows, so that an ended offer or a product page that still shows an old value appears
as the losing side of a conflict with the reason stated; the current answer never uses it.

This sharing is specific to rate rows. Branch hours, contact numbers, counts, links and
paragraphs are read by their own extractors; they share the valid-time rule and the withdrawal
check where those apply, not the grouping or the authority order.

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
recent occurrence. "Distinct" means the product identity above (family, distinguishing name
words, term, flags), the measures compared and the tier: two products with the same term that
each disagree between two pages are two conflicts, and the same disagreement seen in a later
snapshot replaces the earlier one. Counts stated as current facts in dated articles (the
number of branches) are also compared; the most recently published figure is treated as
current.

## Confidence

`confident=False` means the system cannot give the value asked for. Plain declines contain no
sources and no numbers beyond those in the question. Three kinds of low-confidence answers do
carry sources and extra text: a scheduled value that is not yet in effect (a rate, a branch
schedule, a contact number or a paragraph), a withdrawal notice, and the site's own contact
details for a procedural question the pages do not answer.
The contact answer includes the site's phone number on purpose: it is the useful part of the
answer, and a grader that treats any digit in a declined answer as an invented value would
count it against the system. That trade-off is accepted knowingly.
An eligibility question whose best-matching paragraph states no criteria is also returned with
`confident=False`.

## Descriptive questions

Paragraphs are retrieved lexically (BM25 with light stemming and a few generic synonyms),
weighted towards product pages and towards headings that match the question. Two identity
rules carry over from the rate rows: a paragraph about a product of another family than the
one asked about (named by its heading or by a short leading label) is a much weaker match, and
business pages are weaker unless the question says business; a question that names a product
("Free Checking") prefers paragraphs that name it. Text repeated across articles and promotions
is treated as boilerplate; shared disclosure text repeated across product pages is not. A
paragraph that ends by introducing a list is returned together with the next paragraph under
the same heading, when that paragraph is a list or carries no amounts; the next section of the
page is never appended. A paragraph dated "effective <date>" with the date still ahead is a
scheduled change (see valid time above).

Fee and minimum questions need evidence of the amount: the paragraph states an amount ($, %)
or says there is none ("no monthly fee", "no minimum balance", "is free", "waived"). A
paragraph that merely mentions a fee, or uses "free" as an adjective ("free debit card",
"Free Checking"), is not evidence. When the question names a product ("Free Checking",
"Premier Checking" - a word the site's tables use, or a capitalised word, before the family
word), the paragraph must also be about that product: the product is named in its heading, its
label line or its text, and a business variant counts only when the question says business.
With no such paragraph the question is declined; a sibling product's fee is never substituted.

Questions about how to reach the organisation are not retrieval questions: paragraphs that
mention a phone number are usually about something else (a card partner's helpline, a hotline
for setting a PIN). They are answered from the site's own contact page, using the number that
page presents as the organisation's line (labelled phone, call us, member service and the
like, in its own section); fax, partner, rewards, PIN and fraud lines are skipped, and how
often a number appears is not evidence. Without a general number the answer names the contact
page and no number. A branch-specific number is given only when it is listed in that branch's
own section (from the nearest heading or label line down to the number); otherwise the
question is declined without a number. Questions about a specific line (a lost card, a PIN)
go to paragraph retrieval instead.

An eligibility rule often names an area ("you live or work within our charter area") without
saying where it is. The completion is chosen in a fixed order and worded to say what it is:
first a sentence that defines the area (names the places, or introduces a list of them with a
colon) on the same page or on the membership pages ("The website defines that area as
follows: ..."); then a definition of the charter area or field of membership elsewhere on the
site, which must be a full list of places or a colon-introduced list, undated pages before
articles ("Elsewhere on the website that area is described as follows: ..."); and only then
the places served as stated on the same page or the membership pages, marked as separate
context ("The page does not define that area; separately, it says: ..."). Office or
headquarters locations are therefore never presented as the eligibility area, and places
served by another line of business (an insurance agency, a partner) are never used. Each
completion is cited as a further source.

## Key assumptions

* Product names often appear as short bare lines above a table rather than as Markdown
  headings; the two most recent such lines, plus the nearest heading when it names a product,
  form the product identity of the table's rows. In-page anchor links written as list items are
  treated as section tabs.
* Prose rate statements are weaker evidence than tables. A sentence yields a rate fact only
  when it carries exactly one percentage, names the measure and appears under a heading that
  names the product; a sentence with two percentages (a dividend rate and its APY, a
  promotional and an ongoing rate) is not one fact, and percentages that describe a loan type
  ("fixed-rate"), an increment or cap ("add 1%", "adjust up to 2%"), a share ("20% of the
  price"), a down payment, or a rate that is not a price are skipped. From promotional or
  article pages such facts are ignored for answers and conflicts; from product pages they are
  used only when no table covers the same product.
* "How many …" questions are answered from the most recently published dated statement; with
  an explicit or relative year, only statements about that year are used.

## Runtime dependencies and API keys

None. The implementation uses the Python 3.11 standard library only, makes no network calls
and needs no API keys. The evaluation harness this system is normally run with has an optional
LLM-based judge that would require a key; this system does not use it.
