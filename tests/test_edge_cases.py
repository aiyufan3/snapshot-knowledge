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


class EffectiveDateScopeTests(unittest.TestCase):
    """An explicit 'effective <date>' binds the statement next to it, in every format and position, on every path."""

    Q = "What is the 6-month certificate APY?"

    def _before(self, date, before=True):
        lines = ["# Certificates", ""]
        if before:
            lines += [f"Rates are effective as of {date} and are subject to change without notice.", ""]
        lines += ["| Product | APY |", "| --- | --- |", "| 6-month certificate | 4% |", ""]
        if not before:
            lines += [f"Rates are effective as of {date}.", ""]
        return "\n".join(lines)

    def test_date_line_before_the_table_binds_to_it(self):
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", self._before("03/01/2026")),
                                         page("/personal/certificates", "Certificates", table("6-month certificate", 3, "01/01/2026"))]))
        for day, want, other in ((None, "3%", "4%"), ("2026-02-28", "3%", "4%"), ("2026-03-01", "4%", "3%"), ("2026-03-05", "4%", "3%")):
            a = ks.answer(self.Q, as_of=day)
            self.assertTrue(a.confident, (day, a.text))
            self.assertIn(want, a.text, day)
            self.assertNotIn(other, a.text, day)
        self.assertIn("2026-03-01", ks.answer(self.Q).conflict_note or "")
        self.assertTrue(ks.answer("Do any 6-month certificate tiers pay 4% APY?").text.startswith("No"))

    def test_iso_date_is_read(self):
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", self._before("2026-03-01", before=False)),
                                         page("/personal/certificates", "Certificates", table("6-month certificate", 3, "2026-01-01"))]))
        self.assertIn("3%", ks.answer(self.Q).text)
        self.assertNotIn("4%", ks.answer(self.Q).text)
        self.assertIn("4%", ks.answer(self.Q, as_of="2026-03-05").text)

    def test_date_does_not_leak_into_the_next_section(self):
        md = self._before("03/01/2026") + "# Money Market\n\n| Account | APY |\n| --- | --- |\n| Money Market | 2% |\n"
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", md)]))
        mm = ks.answer("What is the money market APY?")
        self.assertTrue(mm.confident, mm.text)
        self.assertIn("2%", mm.text)
        cert = ks.answer(self.Q)
        self.assertFalse(cert.confident, cert.text)
        self.assertIn("2026-03-01", cert.text)

    def test_sentence_dated_for_the_future_is_scheduled(self):
        md = "# Alpha certificates\n\n6-month Alpha certificate APY is 4% effective March 1, 2026.\n"
        ks = build(("s1", "2026-01-10", [page("/personal/alpha", "Alpha certificates", md)]))
        q = "What is the 6-month Alpha certificate APY?"
        a = ks.answer(q)
        self.assertFalse(a.confident, a.text)
        self.assertIn("2026-03-01", a.text)
        self.assertIn("4%", ks.answer(q, as_of="2026-03-05").text)
        with_table = table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates") + "\nThe 6-month Alpha certificate APY is 4% effective March 1, 2026.\n"
        ks = build(("s1", "2026-01-10", [page("/personal/alpha", "Alpha certificates", with_table)]))
        a = ks.answer(q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)
        self.assertNotIn("4%", a.text)

    def test_contact_number_dated_for_the_future_is_not_current(self):
        md = "# Contact\n\n**Phone:** 555-010-2000\n\nEffective March 1, 2026, call our new phone number: 555-010-9000.\n"
        ks = build(("s1", "2026-01-10", [page("/contact", "Contact", md)]))
        a = ks.answer("How do I contact you?")
        self.assertIn("555-010-2000", a.text)
        self.assertNotIn("555-010-9000", a.text)
        a = ks.answer("How do I contact you?", as_of="2026-03-05")
        self.assertIn("555-010-9000", a.text)
        self.assertNotIn("555-010-2000", a.text)
        ks = build(("s1", "2026-01-10", [page("/contact", "Contact", "# Contact\n\nEffective March 1, 2026, call our new phone number: 555-010-9000.\n")]))
        a = ks.answer("How do I contact you?")
        self.assertFalse(a.confident, a.text)
        self.assertIn("2026-03-01", a.text)

    def test_hours_dated_for_the_future_are_not_current(self):
        md = ("# Harbor Branch\n\nLobby: Monday-Friday: 9 a.m. to 5 p.m.\n\n"
              "Effective March 1, 2026, our new lobby hours will be:\n\nLobby: Monday-Friday: 10 a.m. to 4 p.m.\n")
        ks = build(("s1", "2026-01-10", [page("/locations/harbor", "Harbor Branch", md)]))
        q = "What are the lobby hours for the Harbor branch?"
        a = ks.answer(q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("9 a.m. to 5 p.m.", a.text)
        self.assertNotIn("10 a.m. to 4 p.m.", a.text)
        self.assertIn("2026-03-01", a.conflict_note or "")
        a = ks.answer(q, as_of="2026-03-05")
        self.assertIn("10 a.m. to 4 p.m.", a.text)
        self.assertNotIn("9 a.m. to 5 p.m.", a.text)
        ks = build(("s1", "2026-01-10", [page("/locations/harbor", "Harbor Branch", "# Harbor Branch\n\nEffective March 1, 2026, our new lobby hours will be:\n\nLobby: Monday-Friday: 10 a.m. to 4 p.m.\n")]))
        a = ks.answer(q)
        self.assertFalse(a.confident, a.text)
        self.assertIn("2026-03-01", a.text)

    def test_event_dates_near_hours_and_phone_lines_are_not_effective_dates(self):
        md = ("# Harbor Branch\n\nThe annual meeting will be held on 03/20/2026.\n\nLobby: Monday-Friday: 9 a.m. to 5 p.m.\n\n"
              "Join us on March 20, 2026 for the celebration.\n\nPhone: 555-010-2000\n")
        ks = build(("s1", "2026-01-10", [page("/contact", "Contact", md)]))
        a = ks.answer("What are the lobby hours for the Harbor branch?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("9 a.m. to 5 p.m.", a.text)
        self.assertIn("555-010-2000", ks.answer("What is your phone number?").text)

    def test_paragraph_dated_for_the_future_is_not_asserted_as_current(self):
        md = "# Checking\n\n## Free Checking\n\nFree Checking has no monthly fee.\n\nEffective March 1, 2026, Free Checking will have a monthly fee of $10.\n"
        ks = build(("s1", "2026-01-10", [page("/personal/checking", "Checking", md)]))
        q = "Is there a monthly fee on Free Checking?"
        a = ks.answer(q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("no monthly fee", a.text)
        self.assertNotIn("$10", a.text)
        self.assertIn("2026-03-01", a.conflict_note or "")
        self.assertIn("$10", ks.answer(q, as_of="2026-03-05").text)
        ks = build(("s1", "2026-01-10", [page("/personal/checking", "Checking", "# Checking\n\n## Free Checking\n\nEffective March 1, 2026, Free Checking will have a monthly fee of $10.\n")]))
        a = ks.answer(q)
        self.assertFalse(a.confident, a.text)
        self.assertIn("2026-01-10", a.text)


class RevocationGuardTests(unittest.TestCase):
    """A withdrawal notice counts only when it is affirmative, in force, and about the product asked for;
    then it binds values that are still published as well as values from removed pages."""

    Q = "What is the 6-month Alpha certificate APY?"

    def setUp(self):
        self.alpha = page("/personal/alpha", "Alpha certificates", table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates"))
        self.rates = page("/rates", "Rates", table("12-month certificate", 2, "01/01/2026"))

    def notice(self, text, fetched="2026-03-15T08:00:00Z"):
        return page("/newsroom/alpha", "Notice", f"# Notice\n\nMarch 2, 2026\n\n{text}\n", fetched=fetched)

    def ghost(self, text, day="2026-03-15"):
        return build(("s1", "2026-01-10", [self.alpha, self.rates]), ("s2", day, [self.rates, self.notice(text)]))

    def test_active_voice_notice_blocks_a_removed_page(self):
        a = self.ghost("We no longer offer Alpha certificates. These certificates are permanently discontinued.").answer(self.Q)
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)
        self.assertIn("3%", a.conflict_note or "")

    def test_notice_binds_a_value_that_is_still_published(self):
        ks = build(("s1", "2026-01-10", [self.alpha, self.rates, self.notice("The Alpha certificate has been discontinued and is no longer offered.", fetched="2026-01-10T08:00:00Z")]))
        a = ks.answer(self.Q)
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)
        self.assertIn("3%", a.conflict_note or "")
        self.assertIn(SITE + "/newsroom/alpha", [s.url for s in a.sources])
        yes = ks.answer("Do any 6-month Alpha certificate tiers pay 3% APY?")
        self.assertFalse(yes.confident, yes.text)
        self.assertFalse(yes.text.startswith("Yes"), yes.text)

    def test_negated_notice_is_not_a_withdrawal(self):
        a = self.ghost("The Alpha certificate has not been discontinued and remains available.").answer(self.Q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)

    def test_future_dated_notice_applies_only_from_its_date(self):
        ks = self.ghost("The Alpha certificate will be discontinued effective March 1, 2026.", day="2026-01-20")
        a = ks.answer(self.Q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)
        a = ks.answer(self.Q, as_of="2026-03-05")
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)

    def test_historical_question_precedes_the_notice(self):
        ks = self.ghost("The Alpha certificate has been discontinued and is no longer offered.")
        a = ks.answer(self.Q, as_of="2026-02-01")
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)
        self.assertFalse(ks.answer(self.Q).confident)

    def test_another_products_notice_does_not_apply(self):
        text = "The Beta certificate has been discontinued and is no longer offered."
        for ks in (self.ghost(text), build(("s1", "2026-01-10", [self.alpha, self.rates, self.notice(text, fetched="2026-01-10T08:00:00Z")]))):
            a = ks.answer(self.Q)
            self.assertTrue(a.confident, a.text)
            self.assertIn("3%", a.text)

    def test_named_notice_does_not_block_the_generic_question(self):
        ks = build(("s1", "2026-01-10", [page("/rates", "Rates", table("6-month certificate", 2, "01/01/2026")),
                                         self.notice("The Alpha certificate has been discontinued.", fetched="2026-01-10T08:00:00Z")]))
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("2%", a.text)

    def test_family_wide_notice_applies_to_the_named_product(self):
        ks = build(("s1", "2026-01-10", [self.alpha, self.notice("Certificates are no longer offered; existing certificates run to maturity.", fetched="2026-01-10T08:00:00Z")]))
        a = ks.answer(self.Q)
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("3%", a.text)

    def test_penalty_and_maturity_wording_is_not_a_withdrawal(self):
        md = table("6-month certificate", 3, "01/01/2026") + "\nCertificates withdrawn before maturity are subject to a penalty. A certificate is closed at maturity unless renewed. The index may no longer be available.\n"
        ks = build(("s1", "2026-01-10", [page("/personal/certificates", "Certificates", md)]))
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertTrue(a.confident, a.text)
        self.assertIn("3%", a.text)


class ConflictIdentityTests(unittest.TestCase):
    """Each product keeps its own conflict entry; the same conflict seen again in a later snapshot is reported once."""

    def pages(self, fetched="2026-01-10T08:00:00Z"):
        return [
            page("/rates/alpha", "Alpha rates", table("6-month Alpha certificate", 4, "01/01/2026", heading="Alpha certificates"), fetched=fetched),
            page("/personal/alpha", "Alpha certificates", table("6-month Alpha certificate", 3, "01/01/2026", heading="Alpha certificates"), fetched=fetched),
            page("/rates/beta", "Beta rates", table("6-month Beta certificate", 5, "01/01/2026", heading="Beta certificates"), fetched=fetched),
            page("/personal/beta", "Beta certificates", table("6-month Beta certificate", 2, "01/01/2026", heading="Beta certificates"), fetched=fetched),
        ]

    def test_two_products_with_the_same_term_keep_two_entries(self):
        cs = build(("s1", "2026-01-10", self.pages())).conflicts()
        self.assertEqual(len(cs), 2, [c.topic for c in cs])
        by_topic = {("Alpha" if "Alpha" in c.topic else "Beta"): c for c in cs}
        self.assertEqual(sorted(by_topic), ["Alpha", "Beta"])
        self.assertEqual(by_topic["Alpha"].values, ["4.00% APY", "3.00% APY"])
        self.assertEqual(by_topic["Beta"].values, ["5.00% APY", "2.00% APY"])
        self.assertEqual(by_topic["Beta"].resolution, "5.00% APY")

    def test_same_conflict_in_a_later_snapshot_is_reported_once_from_that_snapshot(self):
        cs = build(("s1", "2026-01-10", self.pages()), ("s2", "2026-03-15", self.pages(fetched="2026-03-15T08:00:00Z"))).conflicts()
        self.assertEqual(len(cs), 2, [c.topic for c in cs])
        for c in cs:
            self.assertTrue(all(s.snapshot_id == "s2" for s in c.sources), c.sources)

    def test_tiers_that_do_not_overlap_are_not_merged(self):
        md_a = "# Savings\n\n| Balance | APY |\n| --- | --- |\n| $0 - $9,999 | 1% |\n| $10,000+ | 2% |\n\nRates are effective as of 01/01/2026.\n"
        md_b = "# Savings\n\n| Balance | APY |\n| --- | --- |\n| $0 - $9,999 | 1.5% |\n| $10,000+ | 2.5% |\n\nRates are effective as of 01/01/2026.\n"
        cs = build(("s1", "2026-01-10", [page("/rates", "Rates", md_a), page("/personal/savings", "Savings", md_b)])).conflicts()
        self.assertEqual(len(cs), 2, [(c.topic, c.values) for c in cs])
        self.assertEqual(sorted(tuple(c.values) for c in cs), [("1.00% APY", "1.50% APY"), ("2.00% APY", "2.50% APY")])


class ScopeDefinitionTests(unittest.TestCase):
    """What completes an eligibility rule: an explicit definition of the area first; office locations only as
    separated context; never the places served by another line of business; and no continuation across sections."""

    RULE = ("# Join\n\n## You're Eligible to Join If Any of These Sound Like You\n\n"
            "- You live or work within our charter area.\n\n- You have an immediate family member who is a member.\n\n")
    Q = "Who is eligible to become a member?"

    def test_explicit_definition_beats_office_locations_on_the_same_page(self):
        md = self.RULE + "## About us\n\nOur offices are located in Northvale, Westmere and Eastbrook. Our charter area is Harbor County only.\n"
        a = build(("s1", "2026-01-10", [page("/about/join", "Join", md)])).answer(self.Q)
        self.assertTrue(a.confident, a.text)
        self.assertIn("defines that area", a.text)
        self.assertIn("Harbor County", a.text)
        self.assertNotIn("Northvale", a.text)

    def test_office_locations_alone_are_given_as_separated_context(self):
        md = self.RULE + "## About us\n\nOur offices are located in Northvale, Westmere and Eastbrook.\n"
        a = build(("s1", "2026-01-10", [page("/about/join", "Join", md)])).answer(self.Q)
        self.assertIn("does not define that area", a.text)
        self.assertIn("Northvale", a.text)
        self.assertNotIn("defines that area", a.text)

    def test_places_served_by_another_line_of_business_are_not_used(self):
        md = self.RULE + "## Insurance\n\nOur insurance agency serves members in Northvale, Westmere and Eastbrook.\n"
        a = build(("s1", "2026-01-10", [page("/about/join", "Join", md)])).answer(self.Q)
        self.assertIn("live or work", a.text)
        self.assertNotIn("Northvale", a.text)

    def test_definition_on_another_page_is_used_and_attributed(self):
        other = ("# Scholarships\n\n**Charter Area:** The entrant's permanent home address must be located in one of the following counties:\n\n"
                 "  - Northvale: Any county\n\n  - Westmere: Carroll, Rock Island, Stephenson\n\n**Grade Point Average:** A cumulative GPA of 2.5 is required.\n")
        ks = build(("s1", "2026-01-10", [page("/about/join", "Join", self.RULE), page("/scholarships", "Scholarships", other)]))
        a = ks.answer(self.Q)
        self.assertIn("Elsewhere on the website", a.text)
        self.assertIn("Westmere: Carroll, Rock Island, Stephenson", a.text)
        self.assertNotIn("Grade Point Average", a.text)
        self.assertEqual([s.url for s in a.sources], [SITE + "/about/join", SITE + "/scholarships", SITE + "/scholarships"])

    def test_colon_continuation_stays_inside_the_section(self):
        md = "# Join\n\n## Eligibility\n\nYou are eligible to join if you live in one of the following:\n\n## Auto Loans\n\nRates as low as 8.25% APR on new vehicles.\n"
        a = build(("s1", "2026-01-10", [page("/about/join", "Join", md)])).answer(self.Q)
        self.assertNotIn("8.25", a.text)
        self.assertNotIn("Auto Loans", a.text)


if __name__ == "__main__":
    unittest.main()
