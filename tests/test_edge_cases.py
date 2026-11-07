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


class ProseStatementTests(unittest.TestCase):
    """A percentage in a sentence is a product rate only when the sentence states one."""

    HOME = (
        "# Home Loans\n\n"
        "| Loan | Rate | APR |\n| --- | --- | --- |\n"
        "| 30-Year Fixed Conventional | 7.25% | 7.29% |\n| 15-Year Fixed Conventional | 6.75% | 6.88% |\n\n"
        "Rates are effective as of 01/05/2026.\n\n"
        "### Build Your Dream Home\n\n"
        "- After 12 months the loan converts to an adjustable-rate mortgage. Your rate may adjust up to 2% every 12 months.\n\n"
        "### Your First Home Starts Here\n\n"
        "- Down payments on fixed-rate home loans usually range from 3% to 20%.\n\n"
        "Home Loan Rates: Loan annual percentage rates (APR) depend on credit qualification. Mini-Max Loans: Add 1% to conventional rates.\n"
    )

    def test_down_payments_rate_caps_and_increments_are_not_rates(self):
        ks = build(("s1", "2026-01-10", [page("/personal/loans/home-loans", "Home Loans", self.HOME)]))
        a = ks.answer("What are the home loan rates?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("7.25%", a.text)
        self.assertIn("6.75%", a.text)
        self.assertFalse({1.0, 2.0, 3.0, 20.0} & numbers(a.text), a.text)

    def test_sentence_with_two_measures_is_not_read_as_one_rate(self):
        md = (
            "# Money Market\n\n| Tier | Dividend Rate | APY |\n| --- | --- | --- |\n| $0.01+ | 0.40% | 0.40% |\n\n"
            "Rates are effective as of 01/05/2026.\n\n"
            "### Save Your Money, Your Way\n\n"
            "Open a new money market account with $25,000 in qualifying funds to earn an introductory 4.00% dividend rate for 6 months. 3.66% APY.\n"
        )
        ks = build(("s1", "2026-01-10", [page("/personal/savings-account", "Savings", md)]))
        a = ks.answer("What are the rates on your money market accounts?")
        self.assertIn("0.40%", a.text)
        self.assertFalse({4.0, 3.66} & numbers(a.text), a.text)

    def test_sentence_under_a_product_heading_is_a_rate_statement(self):
        md = (
            "# Savings\n\n| Account | Dividend Rate | APY |\n| --- | --- | --- |\n| Share Savings | 0.10% | 0.10% |\n\n"
            "Rates are effective as of 01/05/2026.\n\n"
            "### Youth Savings\n\nEarn a competitive 1.00% APY on your first $500.\n\n"
            "### Why members love us\n\nMembers earn a competitive 5.00% APY on balances when they bring new money.\n"
        )
        ks = build(("s1", "2026-01-10", [page("/personal/savings-account", "Savings", md)]))
        a = ks.answer("What are your savings rates?")
        self.assertIn("0.10%", a.text)
        self.assertIn("Youth Savings: 1.00% APY", a.text)
        self.assertNotIn("5.00%", a.text)

    def test_sentence_is_used_when_no_table_covers_the_product(self):
        md = "# Rewards Card\n\n- Competitive rate if you carry a balance: 15.50%-21.00% APR.\n"
        ks = build(("s1", "2026-01-10", [page("/personal/credit-cards", "Credit Cards", md)]))
        a = ks.answer("What is the APR on the rewards card?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("15.50%", a.text)


class EligibilityScopeTests(unittest.TestCase):
    """An eligibility rule that names an area ("our charter area") is completed with the page's own description of that area."""

    JOIN = (
        "# Ready to Become Our Newest Member?\n\n"
        "We are so glad you are here! When you join, you become a member-owner.\n\n"
        "#### Local Service\n\n"
        "You can find us in Northvale, Westmere and Eastbrook. If you travel, you can also use shared branches nationwide.\n\n"
        "## You're Eligible to Join If Any of These Sound Like You\n\n"
        "- You live or work within our charter area.\n\n"
        "- You have an immediate family member who is a member.\n\n"
        "To join, simply open a savings account with $25 or more.\n"
    )

    def test_eligibility_answer_names_the_area_the_same_page_describes(self):
        ks = build(("s1", "2026-01-10", [page("/about/join", "Join", self.JOIN)]))
        a = ks.answer("Who is eligible to become a member?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("live or work", a.text)
        for name in ("Northvale", "Westmere", "Eastbrook"):
            self.assertIn(name, a.text)
        self.assertTrue(a.sources and all(s.url == SITE + "/about/join" for s in a.sources), a.sources)

    def test_area_is_not_borrowed_from_an_unrelated_page(self):
        join = self.JOIN.replace("You can find us in Northvale, Westmere and Eastbrook. ", "")
        blog = "# Agency news\n\nOur insurance agency operates in Northvale, Westmere and Eastbrook.\n"
        ks = build(("s1", "2026-01-10", [page("/about/join", "Join", join), page("/learn/blog/agency-news", "Agency news", blog)]))
        a = ks.answer("Who is eligible to become a member?")
        self.assertIn("live or work", a.text)
        self.assertNotIn("Northvale", a.text)

    def test_list_introduced_by_a_colon_is_included(self):
        md = (
            "# Scholarships\n\n"
            "**Charter Area:** The entrant's permanent home address must be located in one of the following counties:\n\n"
            "  - Northvale: Any county\n\n"
            "  - Westmere: Carroll, Rock Island, Stephenson\n\n"
            "**Grade Point Average:** A cumulative GPA of 2.5 is required.\n"
        )
        ks = build(("s1", "2026-01-10", [page("/scholarships", "Scholarships", md)]))
        a = ks.answer("What counties are in the charter area?")
        self.assertIn("Westmere: Carroll, Rock Island, Stephenson", a.text)


class PassageIdentityTests(unittest.TestCase):
    """A descriptive question about a named account is answered about that account."""

    def test_named_account_question_is_not_answered_from_another_account(self):
        personal = (
            "# Checking\n\n| Account | Dividend Rate | APY |\n| --- | --- | --- |\n| Free Checking | 0.00% | 0.00% |\n| Premier Checking | 0.10% | 0.10% |\n\n"
            "## Free Checking\n\nThis account keeps things simple. No monthly fees, no minimum balance and no hoops to jump through.\n\n"
            "## Premier Checking\n\n- Avoid a $10 monthly fee by keeping a monthly balance of $5,000 or more.\n"
        )
        business = (
            "# Business Checking\n\n## Business Premier Checking\n\n"
            "- Skip the $10 monthly fee by keeping your monthly balance above $5,000.\n- Fee-free withdrawals at our ATMs.\n"
        )
        ks = build(("s1", "2026-01-10", [
            page("/personal/checking-accounts", "Checking", personal),
            page("/business/checking-accounts", "Business Checking", business),
        ]))
        a = ks.answer("Is there a monthly fee on Free Checking?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("No monthly fees", a.text)
        self.assertNotIn("$10", a.text)


class ContactTests(unittest.TestCase):
    """How to reach the organisation is answered from its own contact page, never from a paragraph that
    happens to mention a phone number."""

    PAGES = [
        page("/contact", "Contact", "# We're Here to Help\n\nFor general questions, use our form.\n\n**Phone:** [555-010-2000](tel:555-010-2000)\n\n**Routing Number:** 123456789\n\n### Harbor Branch\n\n12 Harbor Rd., Harbor, ST, 10001\n"),
        page("/personal/credit-cards", "Credit Cards", "# Rewards Card\n\n- [What if there is a problem with my reward redemption?](https://www.harborlight-fcu.example/personal/credit-cards#)\n\nRedemptions are handled by our partner. You can contact the partner by calling 555-010-9999 or through the partner's own website.\n"),
    ]

    def setUp(self):
        self.ks = build(("s1", "2026-01-10", self.PAGES))

    def test_contact_question_uses_the_contact_page_number(self):
        for q in ("How do I contact you?", "What is your phone number?", "How can I reach customer service?"):
            a = self.ks.answer(q)
            self.assertTrue(a.confident, (q, a.text))
            self.assertIn("555-010-2000", a.text, q)
            self.assertNotIn("555-010-9999", a.text, q)
            self.assertEqual([s.url for s in a.sources], [SITE + "/contact"], q)

    def test_phone_for_a_branch_without_a_listed_number_is_declined_without_numbers(self):
        a = self.ks.answer("What is the phone number for the Harbor branch?")
        self.assertFalse(a.confident, a.text)
        self.assertFalse(numbers(a.text), a.text)

    def test_procedural_gap_still_points_to_the_contact_channel(self):
        a = self.ks.answer("How can I update my mailing address?")
        self.assertFalse(a.confident, a.text)
        self.assertIn("555-010-2000", a.text)


if __name__ == "__main__":
    unittest.main()
