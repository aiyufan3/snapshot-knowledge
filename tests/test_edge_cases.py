"""Counter-example tests: valid time, explicit revocation, one truth for every path,
product identity and unknown products.  All data is invented and built in memory.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest
from types import SimpleNamespace as N

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import solution  # noqa: E402

SITE = "https://www.harborlight-fcu.example"
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers(text):
    return {float(x) for x in NUM_RE.findall(text or "")}


def page(path, title, markdown, fetched="2026-01-10T08:00:00Z"):
    return N(url=SITE + path, title=title, markdown=markdown, fetched_at=fetched)


def table(product, value, effective, heading="Certificates", label_line=None):
    lines = [f"# {heading}", ""]
    if label_line:
        lines += [label_line, ""]
    lines += ["| Product | APY |", "| --- | --- |", f"| {product} | {value}% |", "",
              f"Rates are effective as of {effective} and are subject to change without notice.", ""]
    return "\n".join(lines)


def build(*snapshots):
    ks = solution.build()
    for sid, day, pages in snapshots:
        ks.ingest(N(snapshot_id=sid, taken_on=day, pages=pages))
    return ks


class ValidTimeTests(unittest.TestCase):
    """A table observed on 10 January that says 'effective 1 March' is not yet in effect."""

    def setUp(self):
        self.ks = build(("s1", "2026-01-10", [
            page("/rates", "Rates", table("6-month certificate", 4, "03/01/2026")),
            page("/personal/certificates", "Certificates", table("6-month certificate", 3, "01/01/2026")),
        ]))

    def test_future_dated_table_is_not_the_current_value(self):
        a = self.ks.answer("What is the 6-month certificate APY?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)
        self.assertNotIn("4%", a.text)
        self.assertEqual([s.url for s in a.sources], [SITE + "/personal/certificates"])
        self.assertIn("2026-03-01", a.conflict_note or "")  # the scheduled change is explained, not used

    def test_future_dated_table_applies_once_its_date_has_passed(self):
        a = self.ks.answer("What is the 6-month certificate APY?", as_of="2026-03-05")
        self.assertIn("4%", a.text)
        self.assertNotIn("3%", a.text)

    def test_yes_no_uses_the_same_in_effect_value(self):
        yes = self.ks.answer("Do any 6-month certificate tiers pay 3.00% APY?")
        self.assertTrue(yes.text.startswith("Yes"), yes.text)
        no = self.ks.answer("Do any 6-month certificate tiers pay 4.00% APY?")
        self.assertTrue(no.text.startswith("No"), no.text)

    def test_scheduled_change_is_not_reported_as_a_contradiction(self):
        self.assertEqual([c for c in self.ks.conflicts() if any("3" in v for v in c.values) and any("4" in v for v in c.values)], [])

    def test_only_a_future_dated_value_is_described_not_asserted(self):
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", table("6-month certificate", 4, "03/01/2026"))]))
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertFalse(a.confident, a.text)
        self.assertIn("2026-03-01", a.text)
        self.assertRegex(a.text, r"not yet in effect|takes effect|scheduled")
        self.assertTrue(a.sources)

    def test_missing_effective_date_means_in_effect(self):
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", "# Certificates\n\n| Product | APY |\n| --- | --- |\n| 6-month certificate | 2% |\n")]))
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertTrue(a.confident)
        self.assertIn("2%", a.text)

    def test_event_dates_are_not_effective_dates(self):
        md = "# Certificates\n\n| Product | APY |\n| --- | --- |\n| 6-month certificate | 2% |\n\nThe annual meeting will be held on 03/20/2026.\n"
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", md)]))
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertTrue(a.confident)
        self.assertIn("2%", a.text)


class OneTruthTests(unittest.TestCase):
    """Ordinary rate answers, yes/no answers and conflicts pick the same value."""

    def setUp(self):
        self.ks = build(("s1", "2026-01-10", [
            page("/rates", "Rates", table("6-month certificate", 4, "01/10/2026")),
            page("/personal/certificates", "Certificates", table("6-month certificate", 3, "01/01/2026")),
        ]))

    def test_rate_sheet_wins_for_every_path(self):
        a = self.ks.answer("What is the 6-month certificate APY?")
        self.assertIn("4%", a.text)
        self.assertNotIn("3%", a.text)
        no = self.ks.answer("Do any 6-month certificate tiers pay 3.00% APY?")
        self.assertTrue(no.text.startswith("No"), no.text)
        self.assertIn("4%", no.text)
        yes = self.ks.answer("Do any 6-month certificate tiers pay 4.00% APY?")
        self.assertTrue(yes.text.startswith("Yes"), yes.text)
        conflicts = [c for c in self.ks.conflicts() if any("4" in v for v in c.values)]
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].resolution.startswith("4"))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.ks = build(("s1", "2026-01-10", [
            page("/personal/alpha", "Alpha certificates", table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates")),
            page("/personal/beta", "Beta certificates", table("6-month Beta certificate", 4, "01/01/2026", heading="Beta certificates")),
        ]))

    def test_differently_named_products_are_not_a_conflict(self):
        self.assertEqual([c for c in self.ks.conflicts() if any("3" in v for v in c.values) and any("4" in v for v in c.values)], [])

    def test_named_product_question_returns_only_that_product(self):
        a = self.ks.answer("What is the 6-month Alpha certificate APY?")
        self.assertIn("3%", a.text)
        self.assertNotIn("4%", a.text)

    def test_unknown_product_is_declined_not_substituted(self):
        q = "What is the 6-month Gamma certificate APY?"
        a = self.ks.answer(q)
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)
        self.assertNotIn("4%", a.text)
        self.assertIn("gamma", a.text.lower())
        self.assertTrue(numbers(a.text) <= numbers(q))

    def test_same_product_on_two_pages_is_comparable(self):
        ks = build(("s1", "2026-01-10", [
            page("/rates", "Rates", table("6-month Alpha certificate", 4, "01/10/2026", heading="Alpha certificates")),
            page("/personal/alpha", "Alpha certificates", table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates")),
        ]))
        hits = [c for c in ks.conflicts() if any("3" in v for v in c.values) and any("4" in v for v in c.values)]
        self.assertEqual(len(hits), 1)


class RevocationTests(unittest.TestCase):
    """A removed page must not keep answering once the site says the product is gone."""

    def setUp(self):
        alpha = page("/personal/alpha", "Alpha certificates", table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates"))
        notice = page("/newsroom/alpha-retired", "Alpha certificate retired",
                      "# Alpha certificate retired\n\nMarch 2, 2026\n\nThe Alpha certificate has been permanently discontinued and is no longer offered. "
                      "Existing certificates run to maturity.\n", fetched="2026-03-15T08:00:00Z")
        fees = page("/fees", "Fees", "# Fees\n\nLost debit card replacement fee: $5.00\n")
        self.ks = build(
            ("s1", "2026-01-10", [alpha, fees, page("/rates", "Rates", table("12-month certificate", 2, "01/01/2026"))]),
            ("s2", "2026-03-15", [notice, page("/rates", "Rates", table("12-month certificate", 2, "01/01/2026"), fetched="2026-03-15T08:00:00Z")]),
        )

    def test_explicit_revocation_blocks_the_last_seen_value(self):
        a = self.ks.answer("What is the 6-month Alpha certificate APY?")
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)
        self.assertRegex(a.text.lower(), r"no longer offered|discontinued|retired")
        self.assertIn(SITE + "/newsroom/alpha-retired", [s.url for s in a.sources])
        self.assertIn("3%", a.conflict_note or "")  # last observed value is kept apart from the current status

    def test_historical_question_still_reads_the_old_snapshot(self):
        a = self.ks.answer("What is the 6-month Alpha certificate APY?", as_of="2026-02-01")
        self.assertTrue(a.confident)
        self.assertIn("3%", a.text)

    def test_plain_missing_page_is_still_answered_with_last_seen_note(self):
        a = self.ks.answer("How much is a lost debit card replacement?")
        self.assertTrue(a.confident)
        self.assertIn("$5.00", a.text)
        self.assertIn("no longer on the website", a.text)

    def test_same_url_update_never_uses_the_old_version(self):
        ks = build(
            ("s1", "2026-01-10", [page("/rates", "Rates", table("6-month certificate", 3, "01/01/2026"))]),
            ("s2", "2026-03-15", [page("/rates", "Rates", table("6-month certificate", 4, "03/01/2026"), fetched="2026-03-15T08:00:00Z")]),
        )
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertIn("4%", a.text)
        self.assertNotIn("3%", a.text)


class PromotionalColumnTests(unittest.TestCase):
    """A table with promotional-period columns next to ongoing columns."""

    def setUp(self):
        md = ("# Money Market\n\nTidewater Money Market\n\n"
              "| Account Type | Balance Tier | Dividend Rate for 1st 6 Months | APY for 1st 6 Months | Dividend Rate After Promotional Period | APY After Promotional Period | Min. to Open |\n"
              "| --- | --- | --- | --- | --- | --- | --- |\n"
              "| Tidewater Money Market | $25,000+ | 4.00% | 3.66% | 3.20% | 3.25% | $25,000 |\n\n"
              "Rates are effective as of 01/02/2026.\n")
        self.ks = build(("s1", "2026-01-10", [page("/earn-more", "Earn more", md)]))

    def test_promotional_columns_are_labelled_and_ongoing_values_are_not(self):
        a = self.ks.answer("What is the Tidewater Money Market APY?")
        self.assertIn("3.25% APY", a.text)
        self.assertIn("3.66% APY (introductory)", a.text)
        self.assertNotIn("3.25% APY (introductory)", a.text)

    def test_standard_and_introductory_questions_select_their_columns(self):
        std = self.ks.answer("What is the standard Tidewater Money Market APY?")
        self.assertIn("3.25%", std.text)
        self.assertNotIn("3.66%", std.text)
        intro = self.ks.answer("What is the introductory Tidewater Money Market APY?")
        self.assertIn("3.66%", intro.text)
        self.assertNotIn("3.25%", intro.text)

    def test_promotional_columns_do_not_create_conflicts_or_yes_answers(self):
        self.assertEqual(self.ks.conflicts(), [])
        no = self.ks.answer("Do any money market tiers pay 3.66% APY?")
        self.assertTrue(no.text.startswith("No"), no.text)


class IntentEvidenceTests(unittest.TestCase):
    def test_eligibility_answer_states_criteria_not_marketing(self):
        ks = build(("s1", "2026-01-10", [
            page("/membership", "Membership", "# Ready to Become Our Newest Member-Owner?\n\nWhen you join, you become a member-owner, unlocking better rates, fewer fees and a money partner that gets you. Ready? Fill out the form below and we will contact you.\n"),
            page("/about/join", "Join", "# Join\n\n## You're Eligible to Join If Any of These Sound Like You\n\n- You live or work in Harbor County.\n\n- You have an immediate family member who is a member.\n"),
        ]))
        a = ks.answer("Who is eligible to become a member?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("live or work", a.text)

    def test_eligibility_without_criteria_evidence_is_low_confidence(self):
        ks = build(("s1", "2026-01-10", [
            page("/membership", "Membership", "# Ready to Become Our Newest Member-Owner?\n\nWhen you join, you become a member-owner, unlocking better rates, fewer fees and a money partner that gets you.\n"),
        ]))
        a = ks.answer("Who is eligible to become a member?")
        self.assertFalse(a.confident, a.text)


if __name__ == "__main__":
    unittest.main()
