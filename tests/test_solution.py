"""Synthetic behaviour tests for the knowledge system.

Everything here runs against the fictional fixture snapshots in ``tests/fixtures`` and
needs only the standard library.  The tests check observable behaviour (what is
answered, cited or declined), not internal structure.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import re
import sys
import types
import unittest
from dataclasses import dataclass

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import solution  # noqa: E402

SITE = "https://www.northwind-cu.example"
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    markdown: str
    fetched_at: str


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    taken_on: str
    pages: list


def load_snapshots(root: pathlib.Path) -> list[Snapshot]:
    out = []
    for d in sorted(p for p in root.iterdir() if (p / "manifest.json").exists()):
        man = json.loads((d / "manifest.json").read_text())
        pages = [
            Page(p["url"], p.get("title", ""), (d / p["file"]).read_text(encoding="utf-8"), p.get("fetched_at", man["taken_on"]))
            for p in man["pages"]
        ]
        out.append(Snapshot(man["snapshot_id"], man["taken_on"], pages))
    out.sort(key=lambda s: s.taken_on)
    return out


def numbers(text: str) -> set:
    return {float(x) for x in NUM_RE.findall(text or "")}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snaps = load_snapshots(HERE / "fixtures" / "snapshots")
        cls.first, cls.latest = cls.snaps[0].snapshot_id, cls.snaps[-1].snapshot_id
        cls.ks = solution.build()
        for s in cls.snaps:
            cls.ks.ingest(s)
        cls.pages = {(s.snapshot_id, p.url): p for s in cls.snaps for p in s.pages}

    def ask(self, q, as_of=None):
        return self.ks.answer(q, as_of)

    def assertRefused(self, ans, question):
        self.assertFalse(ans.confident, ans.text)
        self.assertEqual(ans.sources, [])
        self.assertTrue(numbers(ans.text) <= numbers(question), f"refusal introduced numbers: {ans.text}")
        self.assertNotIn("http", ans.text.lower())

    def assertVerbatimSources(self, sources):
        self.assertTrue(sources)
        for s in sources:
            if not s.quote:
                continue
            page = self.pages.get((s.snapshot_id, s.url))
            self.assertIsNotNone(page, f"source page not in snapshot: {s.snapshot_id} {s.url}")
            haystack = re.sub(r"\s+", " ", solution._clean_inline(page.markdown))
            self.assertIn(re.sub(r"\s+", " ", s.quote).strip(), haystack, f"quote is not an excerpt of {s.url}")


class TemporalTests(Base):
    def test_current_answer_uses_latest_snapshot(self):
        a = self.ask("What is the 6-month certificate APY?")
        self.assertTrue(a.confident)
        self.assertIn("4.10%", a.text)
        self.assertNotIn("4.00%", a.text)
        self.assertEqual({s.snapshot_id for s in a.sources}, {self.latest})

    def test_as_of_uses_snapshot_on_or_before_date(self):
        a = self.ask("What is the 6-month certificate APY?", as_of="2026-02-01")
        self.assertIn("4.00%", a.text)
        self.assertNotIn("4.10%", a.text)
        self.assertEqual({s.snapshot_id for s in a.sources}, {self.first})

    def test_as_of_before_first_snapshot_refuses(self):
        q = "What is the 6-month certificate APY?"
        a = self.ask(q, as_of="2025-06-01")
        self.assertRefused(a, q)

    def test_as_of_after_latest_uses_latest_and_says_so(self):
        a = self.ask("What is the 6-month certificate APY?", as_of="2027-01-01")
        self.assertTrue(a.confident)
        self.assertIn("4.10%", a.text)
        self.assertIn("most recent website snapshot", a.text)

    def test_removed_page_is_answered_with_last_seen_note(self):
        a = self.ask("How much is a lost debit card replacement?")
        self.assertTrue(a.confident)
        self.assertIn("$5.00", a.text)
        self.assertIn("no longer on the website", a.text)
        self.assertEqual({s.snapshot_id for s in a.sources}, {self.first})

    def test_value_changed_in_place_is_not_resurrected(self):
        a = self.ask("What is the APR on a 60-month auto loan for a 2025 vehicle?")
        self.assertIn("5.29%", a.text)
        self.assertNotIn("5.49%", a.text)


class NoFabricationTests(Base):
    QUESTIONS = [
        "What's the URL for the branch and ATM locator?",
        "Who is eligible to become a member?",
        "What is the routing number?",
        "What is the highest Harbor Savings APY?",
        "What are the lobby hours?",
        "What is the 6-month certificate APY?",
        "What is the address of the Harbor branch?",
        "How many branches do you have?",
    ]

    def test_empty_snapshot_refuses_everything(self):
        ks = solution.build()
        ks.ingest(Snapshot("empty", "2026-01-01", []))
        for q in self.QUESTIONS:
            a = ks.answer(q)
            self.assertFalse(a.confident, f"{q!r} -> {a.text}")
            self.assertEqual(a.sources, [])
            self.assertNotIn("http", a.text.lower())

    def test_nothing_ingested_refuses(self):
        ks = solution.build()
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertFalse(a.confident)

    def test_sources_quote_the_page_verbatim(self):
        for q in self.QUESTIONS + ["What credit cards do you offer and what are their APRs?", "What are the drive-up hours?"]:
            a = self.ask(q)
            if a.confident:
                self.assertVerbatimSources(a.sources)


class ProductContextTests(Base):
    def test_table_product_comes_from_label_line_and_model_year_filter(self):
        a = self.ask("What's the APR on a 60-month auto loan for a 2025 vehicle?")
        self.assertIn("5.29%", a.text)
        self.assertNotIn("6.19%", a.text)
        self.assertNotIn("5.69%", a.text)
        self.assertIn(SITE + "/rates", [s.url for s in a.sources])

    def test_personal_and_business_products_are_separate(self):
        a = self.ask("What is the highest Harbor Savings APY?")
        self.assertIn("0.35%", a.text)
        self.assertNotIn("0.25%", a.text)
        b = self.ask("What is the highest business Harbor Savings APY?")
        self.assertIn("0.25%", b.text)
        self.assertNotIn("0.35%", b.text)

    def test_jumbo_tier_only_when_asked(self):
        a = self.ask("What is the APY on the 9-month jumbo special certificate?")
        self.assertIn("4.35%", a.text)
        self.assertNotIn("4.25%", a.text)
        b = self.ask("What is the APY on the 9-month special certificate?")
        self.assertIn("4.25%", b.text)

    def test_standard_rate_excludes_introductory_rate(self):
        a = self.ask("What is the standard APR on the Everyday Visa?")
        self.assertIn("13.50%", a.text)
        self.assertNotIn("1.99%", a.text)

    def test_balance_tier_selection(self):
        a = self.ask("What dividend rate do I earn on Harbor Savings with a balance between $10,000 and $24,999?")
        self.assertIn("0.20%", a.text)
        self.assertNotIn("0.35%", a.text)
        self.assertNotIn("0.10%", a.text)

    def test_term_selection(self):
        a = self.ask("What is the 12-month certificate APY?")
        self.assertIn("3.75%", a.text)
        for other in ("4.10%", "3.25%", "4.25%"):
            self.assertNotIn(other, a.text)

    def test_overview_lists_every_term(self):
        a = self.ask("What are the certificate rates?")
        for v in ("4.10%", "3.75%", "3.25%", "4.25%", "4.35%"):
            self.assertIn(v, a.text)

    def test_card_overview_covers_each_card(self):
        a = self.ask("What credit cards do you offer and what are their APRs?")
        for token in ("Everyday Visa", "Travel Visa", "13.50%", "17.50%"):
            self.assertIn(token, a.text)

    def test_expired_promo_and_blog_prose_are_not_answers(self):
        a = self.ask("What is the 6-month certificate APY?")
        self.assertNotIn("4.25%", a.text)
        self.assertNotIn("3.10%", a.text)

    def test_unlisted_products_are_declined(self):
        for q in ("What is your rate on a 7/1 adjustable-rate mortgage?", "What interest rate do you charge on a construction loan?",
                  "What is the 60-month jumbo certificate rate?"):
            self.assertRefused(self.ask(q), q)

    def test_yes_no_rate_questions(self):
        yes = self.ask("Do any savings tiers pay 0.35% APY?")
        self.assertTrue(yes.text.startswith("Yes"))
        self.assertIn("0.35%", yes.text)
        no = self.ask("Do any savings tiers pay 0.50% APY?")
        self.assertTrue(no.text.startswith("No"))
        self.assertIn("0.35%", no.text)

    def test_paraphrases_agree(self):
        a = self.ask("What is the 6-month certificate APY?")
        b = self.ask("Current APY on a six month certificate?")
        self.assertEqual(numbers(a.text), numbers(b.text))


class ExtractorTests(Base):
    def test_hours_are_bound_to_the_named_branch_and_service(self):
        a = self.ask("What are the lobby hours for the Harbor branch?")
        self.assertTrue(a.confident)
        self.assertIn("9 a.m.-5 p.m.", a.text)
        self.assertNotIn("8 a.m.-6 p.m.", a.text)

    def test_drive_up_hours(self):
        a = self.ask("What are the drive-up hours?")
        self.assertIn("8 a.m.-6 p.m.", a.text)
        self.assertNotIn("9 a.m.-5 p.m.", a.text)

    def test_hours_for_branch_without_hours_are_declined(self):
        q = "Can you give me the lobby hours for the Lakeside branch?"
        a = self.ask(q)
        self.assertRefused(a, q)
        self.assertIn("lakeside", a.text.lower())

    def test_address(self):
        a = self.ask("What is the address of the Harbor branch?")
        self.assertIn("120 Pier Rd", a.text)
        self.assertEqual([s.url for s in a.sources], [SITE + "/contact"])

    def test_routing_number_and_repeat(self):
        a = self.ask("What is the routing number?")
        self.assertIn("123456780", a.text)
        b = self.ask("Can you repeat the routing number?")
        self.assertIn("123456780", b.text)

    def test_count_uses_most_recent_dated_statement(self):
        a = self.ask("How many branches does Northwind have?")
        self.assertIn("14", a.text)
        self.assertNotIn("12 branch", a.text)
        self.assertNotIn("5,000", a.text)  # a shared network's branches are not the organisation's own

    def test_fee_question_without_published_amount_is_declined(self):
        q = "Is there a fee for a lost credit card?"
        self.assertRefused(self.ask(q), q)

    def test_count_resolves_relative_and_explicit_years(self):
        last_year = self.ask("How many employees used Volunteer Day last year?")  # latest snapshot is 2026 -> 2025
        self.assertIn("310", last_year.text)
        self.assertNotIn("280", last_year.text)
        explicit = self.ask("How many employees used Volunteer Day in 2024?")
        self.assertIn("280", explicit.text)
        self.assertNotIn("310", explicit.text)

    def test_count_for_unpublished_year_reports_what_exists(self):
        a = self.ask("How many employees used Volunteer Day in 2023?")
        self.assertIn("No figure for 2023", a.text)
        self.assertIn("310", a.text)

    def test_link_questions_return_site_links(self):
        a = self.ask("What's the URL for the branch and ATM locator?")
        self.assertIn("northwind-cu.example/contact", a.text)
        b = self.ask("How do I join online?")
        self.assertIn("join.northwind-cu.example", b.text)
        self.assertTrue(b.sources)

    def test_descriptive_questions_use_page_text(self):
        a = self.ask("Who is eligible to become a member?")
        self.assertIn("Harbor County", a.text)
        b = self.ask("Can I add my debit and credit cards to a mobile wallet?")
        self.assertIn("Orchard Pay", b.text)

    def test_repeat_calls_are_identical(self):
        for q in NoFabricationTests.QUESTIONS:
            self.assertEqual(self.ask(q).text, self.ask(q).text)


class ConflictTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conflicts = cls.ks.conflicts()

    def find(self, *values):
        return [c for c in self.conflicts if all(any(v in x for x in c.values) for v in values)]

    def test_stale_promo_conflicts_with_rate_sheet(self):
        hits = self.find("4.10%", "4.25%")
        self.assertEqual(len(hits), 1, [c.topic for c in self.conflicts])
        c = hits[0]
        self.assertTrue(c.resolution.startswith("4.10%"), c.resolution)
        self.assertIn("6-month", c.topic.lower())
        urls = {s.url for s in c.sources}
        self.assertIn(SITE + "/rates", urls)
        self.assertIn(SITE + "/promo/spring-certificate-special", urls)
        self.assertEqual({s.snapshot_id for s in c.sources}, {self.latest})  # reported once, latest instance
        self.assertIn(SITE + "/rates", c.reason)

    def test_stale_landing_page_conflicts_with_rate_sheet(self):
        hits = self.find("5.29%", "5.49%")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].resolution.startswith("5.29%"))
        self.assertIn(SITE + "/lp/auto-refi", {s.url for s in hits[0].sources})

    def test_different_products_are_not_merged(self):
        self.assertEqual(self.find("13.50%", "17.50%"), [])

    def test_ordinary_change_between_snapshots_is_not_a_conflict(self):
        self.assertEqual(self.find("0.30%", "0.35%"), [])

    def test_personal_and_business_tiers_are_not_a_conflict(self):
        self.assertEqual(self.find("0.30%", "0.25%"), [])

    def test_conflict_sources_are_verbatim(self):
        for c in self.conflicts:
            self.assertVerbatimSources(c.sources)


class InterfaceTests(Base):
    def test_answer_and_conflict_shapes(self):
        a = self.ask("What is the routing number?")
        for f in ("text", "sources", "confident", "conflict_note"):
            self.assertTrue(hasattr(a, f))
        for s in a.sources:
            for f in ("url", "snapshot_id", "quote"):
                self.assertTrue(hasattr(s, f))
        for c in self.ks.conflicts():
            for f in ("topic", "values", "sources", "resolution", "reason"):
                self.assertTrue(hasattr(c, f))

    def test_stand_alone_answer_str_is_its_text(self):
        a = self.ask("What is the routing number?")
        self.assertEqual(str(a), a.text)

    def test_external_contract_module_is_preferred_when_available(self):
        fake = types.ModuleType("contract")

        @dataclass
        class Source:
            url: str
            snapshot_id: str = None
            quote: str = None

        @dataclass
        class Answer:
            text: str
            sources: list = None
            confident: bool = True
            conflict_note: str = None

        @dataclass
        class Conflict:
            topic: str
            values: list
            sources: list
            resolution: str = None
            reason: str = None

        fake.Source, fake.Answer, fake.Conflict = Source, Answer, Conflict
        saved = sys.modules.get("contract")
        sys.modules["contract"] = fake
        try:
            mod = importlib.reload(solution)
            self.assertIs(mod.Answer, Answer)
            ks = mod.build()
            for s in self.snaps:
                ks.ingest(s)
            self.assertIsInstance(ks.answer("What is the routing number?"), Answer)
            self.assertTrue(all(isinstance(c, Conflict) for c in ks.conflicts()))
        finally:
            if saved is None:
                del sys.modules["contract"]
            else:
                sys.modules["contract"] = saved
            importlib.reload(solution)


if __name__ == "__main__":
    unittest.main()
