"""Knowledge system over dated website snapshots.

The system ingests website snapshots oldest-first and answers questions as the
site stood on a requested date.

* Rate tables and one-line rate statements become *rows* that keep their
  product context (headings plus the short label lines that precede a table),
  tier, term, model-year range, effective date and expiry wording.
* Free text is indexed as paragraph *blocks* (with their heading) for BM25
  retrieval of descriptive answers.
* Links, branch-hours blocks, dated counts and identifiers such as routing
  numbers are read from the pages on demand.

Every stated fact is tied to the page it came from and quotes are verbatim
excerpts.  When the selected snapshot does not support an answer the system
declines (``confident=False``).  ``conflicts()`` reports places where pages in
the same snapshot publish different values for the same product, tier and
measure, for every ingested snapshot.

Standard library only.  No institution names, URLs or answers are hard-coded;
the site's own domain is learned from the ingested pages.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

try:  # The official harness places its ``contract`` module on sys.path.
    from contract import Answer, Conflict, Source  # type: ignore
except ImportError:  # Stand-alone use (tests, demos) gets equivalent stand-ins.

    @dataclass
    class Source:  # type: ignore[no-redef]
        url: str
        snapshot_id: Optional[str] = None
        quote: Optional[str] = None

    @dataclass
    class Answer:  # type: ignore[no-redef]
        text: str
        sources: list = field(default_factory=list)
        confident: bool = True
        conflict_note: Optional[str] = None

        def __str__(self) -> str:  # str() of an answer is its member-facing text
            return self.text

    @dataclass
    class Conflict:  # type: ignore[no-redef]
        topic: str
        values: list
        sources: list
        resolution: Optional[str] = None
        reason: Optional[str] = None


# --------------------------------------------------------------------------
# Vocabulary and patterns
# --------------------------------------------------------------------------

WORD_NUMS = [
    ("eighty four", "84"), ("eighty-four", "84"), ("seventy two", "72"), ("seventy-two", "72"),
    ("forty eight", "48"), ("forty-eight", "48"), ("thirty six", "36"), ("thirty-six", "36"),
    ("twenty four", "24"), ("twenty-four", "24"), ("seventeen", "17"), ("fifteen", "15"),
    ("twelve", "12"), ("eleven", "11"), ("eighteen", "18"), ("sixty", "60"), ("thirty", "30"),
    ("twenty", "20"), ("thirteen", "13"), ("fourteen", "14"), ("sixteen", "16"), ("nineteen", "19"),
    ("ten", "10"), ("nine", "9"), ("eight", "8"), ("seven", "7"), ("six", "6"), ("five", "5"),
    ("four", "4"), ("three", "3"), ("two", "2"),
]
STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of", "on", "in", "for",
    "and", "or", "do", "does", "did", "you", "your", "yours", "me", "my", "i", "we", "our", "us",
    "what", "whats", "how", "much", "many", "can", "could", "would", "should", "please", "tell",
    "give", "get", "with", "about", "from", "that", "this", "it", "its", "at", "by", "as", "if",
    "any", "there", "have", "has", "had", "current", "currently", "right", "now", "today", "s",
    "which", "who", "whom", "when", "where", "why", "will", "may", "might", "want", "need", "like",
    "know", "see", "use", "find", "provide", "all", "some", "into", "than", "then", "so", "not",
    "no", "yes", "am", "pm", "per", "up", "out", "off", "over", "also", "just", "still",
    "receive", "received", "receives", "got", "give", "gives", "given", "make", "makes", "made", "take",
    "takes", "offer", "offers", "offered", "looking", "look", "something", "anything", "things",
    "between", "their", "them", "they", "he", "she", "his", "her", "hers", "these", "those", "other", "another",
    "same", "such", "very", "more", "most", "less", "own", "too", "both", "either", "neither", "whether", "while",
    "during", "after", "before", "since", "until", "within", "without", "through", "against", "around", "under",
    "above", "below", "again", "once", "here", "ever", "never", "always", "often", "already", "yet", "even",
    "rather", "quite", "almost", "enough", "else", "etc", "via", "versus", "regarding", "does", "doing", "done",
    "being", "because", "but", "however", "though", "although", "unless", "where", "whose", "lot", "bit",
}
RATE_WORDS = {"rate", "rates", "apy", "apys", "apr", "aprs", "interest", "dividend", "dividends", "yield"}
GENERIC_NAME_WORDS = {
    "account", "accounts", "loan", "loans", "rate", "rates", "fixed", "regular", "standard", "type",
    "program", "tier", "balance", "term", "product", "products", "deposit", "deposits", "month",
    "months", "year", "years", "only", "option", "options", "new",
}
RATE_KINDS = {"apy", "apr", "rate", "dividend"}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_NAMES = "January|February|March|April|May|June|July|August|September|October|November|December"
LONG_DATE_RE = re.compile(rf"\b({MONTH_NAMES})\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b")
NUM_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")
ISO_DATE_RE = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")
EFFECTIVE_RE = re.compile(
    rf"effective(?: as of)?\s*:?\s*(\d{{1,2}}/\d{{1,2}}/20\d\d|(?:{MONTH_NAMES})\.?\s+\d{{1,2}},?\s+20\d\d)", re.I
)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
PCT_EXPR_RE = re.compile(r"\d+(?:\.\d+)?\s*%(?:\s*(?:[-–—]|to)\s*\d+(?:\.\d+)?\s*%)?")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
TAB_LINK_RE = re.compile(r"^[-*]\s*\[([^\]]{2,60})\]\((?:https?://[^)\s#]*)?#[^)\s]*\)\s*$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
EXPIRED_RE = re.compile(
    r"\b(?:offer|promotion|promo|special|rate|rates|program|certificate)s?\b[^.\n]{0,40}?\b(?:has )?(?:ended|expired|is no longer (?:available|offered|valid))\b"
    r"|\b(?:ended|expired)\b[^.\n]{0,15}?(?:20\d\d|\d{1,2}/\d{1,2}/20\d\d)"
    r"|\(\s*(?:offer|promotion|promo)\s+(?:has )?(?:ended|expired)",
    re.I,
)
EXAMPLE_RE = re.compile(r"\b(example|if you borrow(?:ed)?|illustrative|monthly payment|would be)\b", re.I)
INTRO_HEADER_RE = re.compile(r"intro|promo|\b(?:1st|first)\s+\d+\s+months?\b|for the first\b", re.I)
AFTER_INTRO_RE = re.compile(r"\bafter\b|\bthereafter\b|\bongoing\b|\bstandard\b|\bregular\b", re.I)


def _is_intro_header(header: str) -> bool:
    """Column for a promotional period ("Dividend Rate for 1st 6 Months"); a column described as
    "after the promotional period" is the ongoing rate."""
    h = header or ""
    return bool(INTRO_HEADER_RE.search(h)) and not AFTER_INTRO_RE.search(h)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\b|p\.?\s?m\b)|\bnoon\b|\bmidnight\b", re.I)
DAY_RE = re.compile(r"^\**\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon\b|tue\b|wed\b|thu\b|fri\b|sat\b|sun\b|weekdays?|weekends?|daily)", re.I)
SERVICE_RE = re.compile(r"^\**\s*(lobby|drive[- ]?up|drive[- ]?thr(?:u|ough)|atm)\b\**:?\s*(.*)$", re.I)
QUAL_TEXT_RE = re.compile(
    r"^(?:\$?[\d,.]+\s*(?:[-–—]|to)?\s*\$?[\d,.]*\s*(?:\+|or less|or more|and up|or higher|or under|or over)?|"
    r"\d+\s*-?\s*(?:months?|mos?|years?|yrs?)|(?:model year\s*)?(?:19|20)\d\d\s*[-–—]?\s*\d{0,4}\s*(?:model year)?\s*(?:\(and older\))?|n/?a|none|—|-)$",
    re.I,
)

# (family, pattern): applied most-specific first to row text and to questions.
FAMILY_RULES = [
    ("certificate", r"certificate|\bcds?\b|term[- ]share"),
    ("hsa", r"health savings|\bhsa\b"),
    ("hysa", r"high[- ]yield"),
    ("mma", r"money market"),
    ("share", r"share savings"),
    ("checking", r"checking"),
    ("card", r"credit card|\bvisa\b|mastercard|\bplatinum\b|rewards card"),
    ("equity", r"heloc|home equity"),
    ("improvement", r"home improvement"),
    ("mortgage", r"mortgage|home loan|fixed conventional|adjustable[- ]rate|\barm\b|\brefinance:|\bpurchase:"),
    ("student", r"student"),
    ("recreational", r"\bboat\b|\brvs?\b|\batvs?\b|snowmobile|jet ski|recreational"),
    ("auto", r"\bauto\b|\bcar\b|vehicle|motorcycle"),
    ("personal", r"personal loan"),
    ("ira", r"\bira\b|\broth\b|traditional"),
    ("savings", r"savings"),
]
FAMILY_WORDS = {
    "certificate": {"certificate", "cd", "term", "share"},
    "hsa": {"health", "saving", "hsa"},
    "hysa": {"high", "yield", "saving"},
    "mma": {"money", "market"},
    "share": {"share", "saving"},
    "checking": {"checking"},
    "card": {"credit", "card", "visa", "mastercard"},
    "equity": {"heloc", "home", "equity", "line", "credit"},
    "improvement": {"home", "improvement"},
    "mortgage": {"mortgage", "home", "conventional"},
    "student": {"student"},
    "recreational": {"boat", "rv", "atv", "snowmobile", "jet", "ski", "recreational"},
    "auto": {"auto", "car", "vehicle", "motorcycle"},
    "personal": {"personal"},
    "ira": {"ira"},
    "savings": {"saving"},
    "other": set(),
}
FAMILY_TITLES = {
    "certificate": "certificate", "hsa": "health savings account", "hysa": "high-yield savings",
    "mma": "money market", "share": "share savings", "checking": "checking", "card": "credit card",
    "equity": "home equity", "improvement": "home improvement loan", "mortgage": "home loan",
    "student": "student loan", "recreational": "recreational loan", "auto": "auto loan",
    "personal": "personal loan", "ira": "IRA", "savings": "savings", "other": "",
}
SPECIAL_RE = re.compile(r"certificate special|special certificate|fixed (?:jumbo )?special|special iras?|special (?:ira|jumbo)", re.I)
MUST_MATCH_QUALIFIERS = [  # question wording that names a product type a row must also mention
    (r"\badjustable\b|\barm\b", r"adjustable|\barm\b"),
    (r"\bconstruction\b", r"construction"),
    (r"\bheloc\b|line of credit", r"heloc|line of credit"),
    (r"\bbalance transfer\b", r"balance transfer|transfer"),
]
DEFAULT_REFUSAL = "I don't know. That isn't listed on the website. Please contact us."
# Wording that says a product or offer has been withdrawn (checked in the *current* snapshot
# before a page that disappeared is allowed to answer).
REVOKED_RE = re.compile(
    r"\b(?:discontinued|no longer (?:offered|available|accepting|accepts|open)|has been retired|was retired|retired|"
    r"withdrawn|phased out|sunset|ceased|closed to new|has (?:been )?closed|is closed|replaced by)\b", re.I)
# Question intents whose answer must carry specific evidence (otherwise confidence is lowered).
ELIGIBILITY_Q_RE = re.compile(r"\beligib|\bqualif|\bwho can (?:join|open|become|apply|get)|\brequirements? (?:to|for) (?:join|membership|open)", re.I)
ELIGIBILITY_EVIDENCE_RE = re.compile(
    r"\b(?:live|lives|living|work|works|working|resid\w*|famil\w*|household|employ\w*|belong\w*|"
    r"member of|count(?:y|ies)|area|anyone|open to|must be|citizen|resident)\b", re.I)
# Question words that are never product names (verbs, roles, qualifiers), used when deciding
# whether a question names a product the site does not have.
ENTITY_SKIP = {
    "member", "members", "membership", "customer", "customers", "people", "person", "someone", "anyone", "everyone",
    "online", "mobile", "existing", "please", "dear", "hello", "hi", "community", "federal", "financial", "national",
    "first", "united", "state", "services", "service", "team", "staff", "today", "tomorrow", "yesterday", "week",
    "weeks", "day", "days", "pay", "pays", "paying", "earn", "earns", "earning", "charge", "charges", "cost", "costs",
    "apply", "applies", "open", "opening", "borrow", "borrowing", "deposit", "deposits", "save", "saving", "qualify",
    "available", "list", "listed", "publish", "published", "show", "shows", "mean", "means", "work", "works",
    "jumbo", "special", "specials", "business", "commercial", "intro", "introductory", "standard", "regular", "promo",
    "promotional", "roth", "traditional", "ira", "iras", "highest", "lowest", "best", "top", "maximum", "minimum",
    "purchase", "refinance", "refinancing", "adjustable", "construction", "fixed", "variable", "tier", "tiers",
    "vehicle", "model", "balance", "amount", "current", "annual", "percentage", "yield", "interest",
}
COUNT_VERB_WORDS = {"has", "have", "had", "employs", "employ", "operates", "operate", "serves", "serve", "with", "are", "is",
                    "includes", "including", "owns", "runs", "maintains", "counts", "totals"}
COUNT_QUANTITY_WORDS = {"than", "over", "nearly", "about", "approximately", "of", "total", "currently", "now", "all", "across"}
INSTITUTION_WORDS = {"credit", "union", "bank", "cu", "company", "organization", "organisation", "institution", "cooperative"}
COUNT_BREAKERS = {"of", "in", "on", "at", "for", "to", "from", "outside", "inside", "per", "every", "each", "or",
                  "and", "with", "by", "than", "as", "that", "which", "who", "if", "when", "while", "percent"}
SYNONYMS = {  # small, generic expansions used (at reduced weight) for passage retrieval
    "eligible": ["join", "membership", "qualify"], "eligibility": ["join", "membership", "qualify"],
    "member": ["membership", "join"], "membership": ["member", "join"], "join": ["member", "membership"],
    "fee": ["charge", "cost"], "cost": ["fee", "charge"], "charge": ["fee"],
    "phone": ["call", "telephone"], "call": ["phone"],
    "recognition": ["recogniz", "award", "named"], "award": ["recognition", "recogniz"],
    "close": ["closure", "closing"], "closing": ["close", "closure"], "closure": ["close", "closing"],
}


# --------------------------------------------------------------------------
# Small text helpers
# --------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"(?<=[a-z])\$(?=[a-z])", "s", t)  # "Home$tart" -> "homestart"
    return TOKEN_RE.findall(t)


def _stem(tok: str) -> str:
    if len(tok) <= 3 or tok.isdigit():
        return tok
    if tok.endswith("ies") and len(tok) > 5:
        return tok[:-3] + "y"
    if tok.endswith(("ches", "shes", "sses", "xes")):
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    if tok.endswith("ing") and len(tok) > 6:
        return tok[:-3]
    if tok.endswith("ed") and len(tok) > 5:
        return tok[:-2]
    return tok


def _content(text: str, stop: set) -> list[str]:
    return [_stem(t) for t in _tokens(text) if t not in stop and len(t) > 1]


def _clean_inline(text: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = t.replace("**", "").replace("\\r\\n", " ").replace("\\", "")
    t = re.sub(r"(?<!\w)_|_(?!\w)", "", t)
    t = t.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", t).strip()


def _clean_cell(cell: str) -> str:
    c = (cell or "").replace("–", "-").replace("—", "-").replace("\\", "")
    c = re.sub(r"(%|\bAPR|\bAPY)\s?\d{1,2}\b", r"\1", c)  # footnote markers: "0.20%1", "5.39% APR1"
    return re.sub(r"\s+", " ", c).strip()


def _norm(text: str) -> str:
    t = (text or "").lower().replace("–", "-").replace("—", "-").replace(",", "")
    t = re.sub(r"[^a-z0-9%$./+\- ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _iso(year: int, month: int, day: int) -> str:
    try:
        import datetime as _dt

        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def _long_date_iso(m: re.Match) -> str:
    return _iso(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def _first_date(text: str) -> str:
    """Publication date of an article: the first long-form date near the top."""
    m = LONG_DATE_RE.search(text or "")
    return _long_date_iso(m) if m else ""


def _effective_date(text: str) -> str:
    m = EFFECTIVE_RE.search(text or "")
    if not m:
        return ""
    raw = m.group(1)
    nm = NUM_DATE_RE.search(raw)
    if nm:
        return _iso(int(nm.group(3)), int(nm.group(1)), int(nm.group(2)))
    lm = LONG_DATE_RE.search(raw)
    return _long_date_iso(lm) if lm else ""


def _money_numbers(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?", text or ""):
        out.append(float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")))
    return out


def _year_span(text: str):
    t = (text or "").replace("–", "-").replace("—", "-")
    if not re.search(r"model|year", t, re.I):
        return None
    m = re.search(r"((?:19|20)\d\d)\s*-\s*(\d{2,4})", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b < 100:
            b = (a // 100) * 100 + b
        return (min(a, b), max(a, b))
    m = re.search(r"((?:19|20)\d\d)", t)
    if m:
        y = int(m.group(1))
        if re.search(r"older|prior|earlier", t, re.I):
            return (1900, y)
        if re.search(r"newer|later", t, re.I):
            return (y, 2100)
        return (y, y)
    return None


def _balance_span(text: str):
    t = text or ""
    nums = _money_numbers(t)
    if not nums:
        return None
    low_words = bool(re.search(r"\bor less\b|\bup to\b|\bor under\b", t, re.I))
    high_words = bool(re.search(r"\+|or more|and up|or higher|\bover\b|or greater", t, re.I))
    if len(nums) == 1:
        if high_words:
            return (nums[0], 1e18)
        if low_words:
            return (0.0, nums[0])
        return (nums[0], nums[0])
    lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
    if high_words and hi == nums[1]:
        hi = 1e18 if "+" in t and t.strip().endswith("+") else hi
    return (lo, hi)


def _terms(text: str):
    months, years = set(), set()
    t = text or ""
    for m in re.finditer(r"(\d+)\s*-?\s*(?:months?|mos?)\b", t, re.I):
        months.add(int(m.group(1)))
    for m in re.finditer(r"(\d+)\s*-?\s*(?:years?|yrs?)\b", t, re.I):
        years.add(int(m.group(1)))
    years = {y for y in years if y < 100}  # "model year 2025" is not a term
    return months, years


def _overlaps(a, b) -> bool:
    if not a or not b:
        return False
    return a[0] <= b[1] and b[0] <= a[1]


def _is_qualifier_text(s: str) -> bool:
    return bool(QUAL_TEXT_RE.match(_clean_cell(s)))


def _product_like(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(p, low) for _f, p in FAMILY_RULES)


def _family_of(text: str) -> str:
    low = (text or "").lower()
    for fam, pat in FAMILY_RULES:
        if re.search(pat, low):
            return fam
    return "other"


def _url_product_name(url: str) -> str:
    path = urlparse(url or "").path.rstrip("/")
    seg = path.rsplit("/", 1)[-1]
    seg = re.sub(r"\.(pdf|html?)$", "", seg)
    return seg.replace("-", " ").replace("_", " ")


def _priority(url: str) -> int:
    """Coarse page authority: 3 = rate sheet, 2 = product/info page, 1 = everything else."""
    u = (url or "").lower()
    p = urlparse(u if "//" in u else "https://" + u)
    path = p.path.rstrip("/")
    if re.search(r"calculator|promo|offer|-lp$|/lp$|earn|switch|guidance|/blog|/newsroom|/learn|/news|/press|/20\d\d/|\.pdf$", path):
        return 1
    if path.endswith("/rates") or path.endswith("-rates") or "/rates/" in path + "/":
        return 3
    if re.search(r"^/(personal|business|about|contact|checking|savings|loans|credit-cards|online|membership)", path):
        return 2
    return 1


def _describe_page(url: str, priority: int) -> str:
    u = (url or "").lower()
    if priority == 3:
        return "the official rate sheet"
    if priority == 2:
        return "a product page"
    if re.search(r"/blog|/newsroom|/learn|/news|/press|/20\d\d/", u):
        return "an article"
    if "calculator" in u:
        return "a calculator page"
    if u.endswith(".pdf"):
        return "a PDF document"
    return "a promotional or landing page"


def _percent_values(cell: str):
    raw = (cell or "").replace("–", "-").replace("—", "-")
    found = [float(x) for x in PCT_RE.findall(raw)]
    if not found:
        return None
    if len(found) >= 2 and re.search(r"%\s*(?:-|to)\s*\d", raw):
        return ("range", round(min(found[0], found[1]), 4), round(max(found[0], found[1]), 4))
    return ("point", round(found[0], 4))


def _pct_display(parsed, cell: str) -> str:
    nums = re.findall(r"\d+(?:\.\d+)?(?=\s*%)", (cell or ""))
    if parsed[0] == "range" and len(nums) >= 2:
        return f"{nums[0]}%-{nums[1]}%"
    return f"{nums[0]}%" if nums else f"{parsed[1]:.2f}%"


def _fmt_pct(v: float) -> str:
    s = f"{v:.3f}"
    return (s[:-1] if s.endswith("0") else s) + "%"


def _value_token(parsed) -> str:
    if parsed[0] == "point":
        return _fmt_pct(parsed[1])
    return f"{_fmt_pct(parsed[1])}-{_fmt_pct(parsed[2])}"


def _measure_kind(header: str, cell: str) -> Optional[str]:
    h = (header or "").lower()
    c = (cell or "").lower()
    if re.search(r"point|payment|min\.|minimum|balance|amount|tier|\bterm\b|program|type|loan amount|product|certificate\b|ira type", h) and not re.search(r"\bapy\b|\bapr\b|yield|dividend", h):
        return None
    if "%" not in c and not re.search(r"\d", c):
        return None
    if re.search(r"\bapy\b|annual percentage yield", h):
        return "apy"
    if re.search(r"\bapr\b|annual percentage rate", h):
        return "apr"
    if "dividend" in h:
        return "dividend"
    if re.search(r"\brate\b|interest", h):
        return "rate"
    if "%" in c:
        if re.search(r"\bapy\b", c):
            return "apy"
        if re.search(r"\bapr\b", c):
            return "apr"
        return "percent"
    return None


def _excluded_column(header: str) -> bool:
    return bool(re.search(r"min\.|minimum|payment|points?\b|to open|to earn|category|interest|principal|remaining", (header or "").lower()))


def _is_list_item(s: str) -> bool:
    return bool(re.match(r"^(?:[-*•]|\d+[.)])\s+", (s or "").strip()))


def _is_label_line(s: str) -> bool:
    """Short bare text line that names the table (or group of tables) that follows."""
    if not s or len(s) > 70 or s.startswith(("[", "!", "-", "*", ">", "|", "#", "✕")):
        return False
    if re.match(r"^\d+[.)]\s", s):
        return False
    core = s.strip("*_ ")
    if not core or core.endswith((".", ":", "?", "!")) and not core.endswith(")"):
        return False
    if "%" in core or "$" in core or "http" in core or TIME_RE.search(core):
        return False
    if len(core.split()) > 8 or not re.search(r"[A-Za-z]", core):
        return False
    return True


def _expand_question(question: str) -> str:
    q = (question or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")
    q = re.sub(r"(?<=[a-z])\$(?=[a-z])", "s", q)
    for word, num in WORD_NUMS:
        q = re.sub(rf"\b{re.escape(word)}\b", num, q)
    q = re.sub(r"(\d+)\s*-?\s*mo\b", r"\1 month", q)
    return q


def _question_terms(q: str):
    cleaned = re.sub(r"((?:19|20)\d\d)\s*model[- ]year", " ", q)
    cleaned = re.sub(r"model[- ]year\s*((?:19|20)\d\d)", " ", cleaned)
    cleaned = re.sub(r"\b(last|this|next|previous|per|a|every|each)\s+year\b", " ", cleaned)
    cleaned = re.sub(r"\bas of\b.{0,40}", " ", cleaned)
    return _terms(cleaned)


def _model_years(q: str) -> list[int]:
    years = []
    for m in re.finditer(r"((?:19|20)\d\d)\s*model[- ]year", q):
        years.append(int(m.group(1)))
    for m in re.finditer(r"model[- ]year\s*((?:19|20)\d\d)", q):
        years.append(int(m.group(1)))
    for m in re.finditer(r"\b(?:on|for|buying|financing)\s+an?\s+((?:19|20)\d\d)\b", q):
        years.append(int(m.group(1)))
    return years


def _balance_query(q: str):
    m = re.search(r"between\s+\$?\s*(\d[\d,]*(?:\.\d+)?)\s+and\s+\$?\s*(\d[\d,]*(?:\.\d+)?)", q)
    if m:
        a = float(m.group(1).replace(",", ""))
        b = float(m.group(2).replace(",", ""))
        return (min(a, b), max(a, b))
    m = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(?:\+|and up|or more|or higher|and over)", q)
    if m:
        return (float(m.group(1).replace(",", "")), 1e18)
    m = re.search(r"(?:balance|deposit|have|with)\s+(?:of\s+)?\$\s*(\d[\d,]*(?:\.\d+)?)\b", q) or re.search(
        r"\$\s*(\d[\d,]*(?:\.\d+)?)\s+(?:balance|deposit|in savings|on deposit)", q)
    if m:
        v = float(m.group(1).replace(",", ""))
        return (v, v)
    return None


def _wants_product(q: str) -> Optional[str]:
    fam = _family_of(q)
    return None if fam == "other" else fam


def _family_match(family: str, row: "Row") -> bool:
    if family == "savings":
        return row.family in {"share", "savings", "hysa"}
    if family == "ira":
        return row.is_ira
    if family == "certificate":
        return row.family == "certificate"
    return row.family == family


# --------------------------------------------------------------------------
# Index structures
# --------------------------------------------------------------------------

class Row:
    __slots__ = (
        "snapshot_id", "url", "title", "pub", "fetched_at", "priority", "product", "label", "quals",
        "headers", "cells", "measures", "effective", "expired", "family", "term_months", "term_years",
        "year_span", "balance", "is_ira", "is_business", "is_special", "is_jumbo", "is_intro", "kind",
        "text", "quote", "name_tokens", "subtype", "order", "prose",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def display_name(self) -> str:
        prod = (self.product or "").strip()
        label = (self.label or "").strip()
        parts = []
        if self.kind == "line":
            parts.append(prod or label)
        else:
            if prod and label and not _is_qualifier_text(label):
                pt = {t for t in _tokens(prod) if t not in GENERIC_NAME_WORDS}
                lt = {t for t in _tokens(label) if t not in GENERIC_NAME_WORDS}
                if pt <= lt:
                    parts.append(label)
                elif lt <= pt:
                    parts.append(prod)
                else:
                    parts.append(f"{prod}, {label}")
            else:
                parts.append(prod or label)
                if label and _is_qualifier_text(label) and label.lower() not in {"n/a", "none", "-"}:
                    parts.append(label)
        for qv in self.quals:
            if qv and qv not in parts:
                parts.append(qv)
        name = ", ".join(p for p in parts if p)
        if self.is_business and "business" not in name.lower():
            name += " (business)"
        return name


class SnapIndex:
    def __init__(self, snapshot_id: str, taken_on: str):
        self.snapshot_id = snapshot_id
        self.taken_on = taken_on
        self.rows: list[Row] = []
        self.blocks: list[dict] = []
        self.links: list[tuple] = []
        self.pages: list[dict] = []
        self.postings: dict = defaultdict(list)
        self.idf: dict = {}
        self.avg_len = 1.0
        self.vocab: set = set()
        self.schedules = None


class Knowledge:
    def __init__(self):
        self.snaps: list[SnapIndex] = []
        self.stop: set = set(STOP)
        self.hosts: set = set()
        self.last_seen: dict = {}   # url -> (snapshot_id, taken_on, page)
        self._ghost_pages: dict = {}  # snapshot_id -> {url: (snapshot_id, taken_on, page)}
        self._ghost_index: dict = {}

    # ------------------------------------------------------------------ ingest
    def ingest(self, snapshot) -> None:
        self._learn_site(snapshot)
        idx = SnapIndex(snapshot.snapshot_id, snapshot.taken_on)
        current = set()
        for order, page in enumerate(snapshot.pages):
            current.add(page.url)
            self._ingest_page(idx, page, order)
        self._finish_index(idx)
        self._ghost_pages[idx.snapshot_id] = {u: v for u, v in self.last_seen.items() if u not in current}
        for page in snapshot.pages:
            self.last_seen[page.url] = (snapshot.snapshot_id, snapshot.taken_on, page)
        self.snaps.append(idx)
        self.snaps.sort(key=lambda s: s.taken_on)

    def _learn_site(self, snapshot) -> None:
        for page in snapshot.pages:
            host = urlparse(page.url or "").netloc.lower()
            if not host:
                continue
            self.hosts.add(host)
            labels = host.split(".")
            brand = labels[-2] if len(labels) >= 2 else labels[0]
            for tok in TOKEN_RE.findall(brand):
                if len(tok) > 1:
                    self.stop.add(tok)
                    self.stop.add(tok + "s")

    def _same_site(self, url: str) -> bool:
        host = urlparse(url or "").netloc.lower()
        if not host:
            return False
        tail = ".".join(host.split(".")[-2:])
        return any(".".join(h.split(".")[-2:]) == tail for h in self.hosts)

    def _ingest_page(self, idx: SnapIndex, page, order: int) -> None:
        text = page.markdown or ""
        url = page.url or ""
        title = (page.title or "").strip()
        pinfo = {
            "url": url, "title": title, "text": text, "pub": _first_date(text[:1500]),
            "priority": _priority(url), "fetched_at": (page.fetched_at or "")[:10], "order": order,
            "title_toks": set(_content(title.split("|")[0], self.stop)),
        }
        idx.pages.append(pinfo)
        lines = text.splitlines()
        heading = title.split("|")[0].strip()
        labels: list[str] = []
        para: list[str] = []
        n = len(lines)

        def flush():
            if para:
                self._add_block(idx, pinfo, heading, para)
                para.clear()

        i = 0
        while i < n:
            raw = lines[i]
            s = raw.strip()
            if not s:
                nxt = next((lines[j].strip() for j in range(i + 1, min(n, i + 3)) if lines[j].strip()), "")
                keep = para and _is_list_item(para[-1]) and _is_list_item(nxt)
                # FAQ pattern: a single list item (the question) followed by a plain paragraph (the answer)
                faq = (para and len(para) == 1 and _is_list_item(para[0]) and nxt and not _is_list_item(nxt)
                       and not nxt.startswith(("#", "|", "![", "[", "✕")) and not _is_label_line(nxt))
                if not (keep or faq):
                    flush()
                i += 1
                continue
            for anchor, href in LINK_RE.findall(raw):
                idx.links.append((_clean_inline(anchor), href, f"{heading} {' '.join(labels)}", url, _clean_inline(raw)[:240]))
            if s.startswith("#"):
                flush()
                heading = _clean_inline(s.lstrip("#"))
                labels = []
                i += 1
                continue
            tab = TAB_LINK_RE.match(s)
            if tab:  # "- [Certificate Rates](same-page#anchor)" is a section tab, not content
                flush()
                heading = _clean_inline(tab.group(1))
                labels = []
                i += 1
                continue
            if s.startswith("|"):
                flush()
                i = self._parse_table(idx, pinfo, lines, i, heading, labels)
                if labels:
                    labels.pop()
                continue
            if s.startswith(("![", "✕", "* * *")):
                i += 1
                continue
            if _is_label_line(s):
                flush()
                labels.append(_clean_inline(s))
                labels = labels[-2:]
                i += 1
                continue
            if "%" in s and len(s) < 320:
                self._maybe_line_fact(idx, pinfo, heading, labels, s)
            para.append(s)
            i += 1
        flush()

    def _parse_table(self, idx, pinfo, lines, i, heading, labels) -> int:
        headers = _split_table_row(lines[i])
        i += 1
        if headers is None:
            return i
        if i < len(lines):
            nxt = _split_table_row(lines[i])
            if nxt is not None and _is_separator(nxt):
                i += 1
        body = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = _split_table_row(lines[i])
            if cells and not _is_separator(cells):
                body.append((cells, lines[i].strip()))
            i += 1
        window = []
        j = i
        while j < len(lines) and len(window) < 12:
            s = lines[j].strip()
            if s.startswith(("|", "#")) or (_is_label_line(s) and window):
                break
            if s:
                window.append(s)
            j += 1
        window_text = "\n".join(window)
        effective = _effective_date(window_text)
        expired = bool(EXPIRED_RE.search(window_text))
        for cells, quote in body:
            self._add_row(idx, pinfo, heading, labels, headers, cells, effective, expired, "table", quote)
        return i

    def _add_row(self, idx, pinfo, heading, labels, headers, cells, effective, expired, kind, quote, prose=False):
        label = _clean_cell(cells[0]) if cells else ""
        if not label or len(label) > 180:
            return
        width = max(len(headers), len(cells))
        hdrs = list(headers) + [""] * (width - len(headers))
        vals = list(cells) + [""] * (width - len(cells))
        measures = []
        quals = []
        for j, (h, c) in enumerate(zip(hdrs, vals)):
            kind_m = _measure_kind(h, c)
            if kind_m:
                parsed = _percent_values(c)
                if parsed:
                    measures.append((kind_m, parsed, _pct_display(parsed, _clean_cell(c)), h))
                continue
            if j == 0 or _excluded_column(h):
                continue
            cc = _clean_cell(c)
            if cc and cc.lower() not in {"n/a", "none", "-", ""}:
                quals.append(cc)
        if not measures:
            return
        url = pinfo["url"]
        specific = labels[-1] if labels else ""
        heading_like = heading if _product_like(heading) else ""
        label_desc = not _is_qualifier_text(label)
        ctx_text = " ".join(x for x in [heading_like, *labels] if x)
        if kind == "line":
            product = heading or specific if prose else label
            name_src = product
        else:
            product = specific or heading_like or (label if label_desc else "")
            name_src = " ".join(x for x in [specific or heading_like, label if label_desc else ""] if x)
        if not product:
            product = FAMILY_TITLES.get(_family_of(url), "") or _url_product_name(url)
        if not name_src:
            name_src = _url_product_name(url)
        blob = " ".join([ctx_text, label, *quals, " ".join(hdrs)])
        local = f"{label} {' '.join(quals)}"
        family = _family_of(local)
        if family == "other":
            family = _family_of(ctx_text)
        if family == "other":
            family = _family_of(_url_product_name(url))
        low_blob = blob.lower()
        is_ira = bool(re.search(r"\bira\b|\broth\b|\btraditional\b", low_blob))
        months, years = _terms(blob)
        if family == "ira" and (months or years or "certificate" in low_blob):
            family = "certificate"
        if is_ira and family == "certificate":
            pass
        year_span = _year_span(local)
        balance = None
        for c in quals + ([label] if "$" in label else []):
            if "$" in c:
                balance = _balance_span(c)
                if balance:
                    break
        name_tokens = frozenset(t for t in _content(name_src, self.stop) if t not in GENERIC_NAME_WORDS)
        if not name_tokens:
            name_tokens = frozenset(t for t in _content(_url_product_name(url), self.stop) if t not in GENERIC_NAME_WORDS)
        subtype = frozenset(t for t in name_tokens if t not in FAMILY_WORDS.get(family, set()) and not t.isdigit())
        row = Row(
            snapshot_id=idx.snapshot_id, url=url, title=pinfo["title"], pub=pinfo["pub"],
            fetched_at=pinfo["fetched_at"], priority=pinfo["priority"], product=product, label=label,
            quals=quals, headers=tuple(hdrs), cells=tuple(vals), measures=measures, effective=effective,
            expired=expired or bool(EXPIRED_RE.search(local)), family=family, term_months=months,
            term_years=years, year_span=year_span, balance=balance, is_ira=is_ira,
            is_business="business" in low_blob or "/business" in url.lower(),
            is_special=bool(SPECIAL_RE.search(blob)), is_jumbo="jumbo" in low_blob,
            is_intro=bool(re.search(r"\bintro", f"{label} {' '.join(quals)}".lower())), kind=kind,
            text=_norm(f"{blob} {_url_product_name(url)}"), quote=_clean_inline(quote)[:240],
            name_tokens=name_tokens, subtype=subtype, order=len(idx.rows), prose=prose,
        )
        idx.rows.append(row)
        if not prose:
            idx.vocab.update(name_tokens)

    def _maybe_line_fact(self, idx, pinfo, heading, labels, line):
        if line.count("%") > 3 or EXAMPLE_RE.search(line):
            return
        m = PCT_EXPR_RE.search(line)
        if not m:
            return
        prefix = _clean_inline(line[:m.start()]).strip(" :-*•")
        prefix = re.sub(r"\s+(?:of|at|is|as low as|starting at|from)$", "", prefix, flags=re.I)
        if len(prefix) < 3 or len(prefix) > 160 or not re.search(r"[A-Za-z]", prefix):
            return
        prose = not _product_like(prefix) or len(_tokens(prefix)) > 6
        low = line.lower()
        if re.search(r"\bapy\b|annual percentage yield", low):
            kind = "apy"
        elif re.search(r"\bapr\b|annual percentage rate", low):
            kind = "apr"
        elif "dividend" in low:
            kind = "dividend"
        elif re.search(r"\brate\b|\binterest\b", low):
            kind = "rate"
        else:
            return
        cell = _clean_cell(m.group(0))
        headers = ["Product", kind.upper()]
        self._add_row(idx, pinfo, heading, labels, headers, [prefix, cell], "", bool(EXPIRED_RE.search(line)), "line", line, prose=prose)

    def _add_block(self, idx, pinfo, heading, para):
        joined = " ".join(para)
        n_links = len(LINK_RE.findall(joined))
        clean = _clean_inline(joined)
        non_link = _clean_inline(LINK_RE.sub(" ", joined))
        if len(clean) < 25 or (n_links >= 3 and len(clean) < 400) or clean.lower().count("http") > 1:
            return
        if n_links >= 1 and len(non_link) < 15:  # navigation / teaser links are not content
            return
        body = _content(clean, self.stop)
        head = _content(heading, self.stop)
        toks = body + head + head  # headings summarise the section: count them twice
        if len(body) < 3:
            return
        lead = pinfo.setdefault("_blocks", 0) == 0 and len(clean) >= 80
        pinfo["_blocks"] = pinfo.get("_blocks", 0) + 1
        idx.blocks.append({
            "text": clean[:2000], "heading": heading, "url": pinfo["url"], "title": pinfo["title"],
            "pub": pinfo["pub"], "fetched_at": pinfo["fetched_at"], "priority": pinfo["priority"],
            "tf": Counter(toks), "len": len(toks), "norm": clean.lower()[:160], "dup": 1,
            "lead": lead, "pos": pinfo["_blocks"], "title_toks": pinfo["title_toks"], "body": set(body),
        })

    def _finish_index(self, idx: SnapIndex) -> None:
        df: Counter = Counter()
        dup: Counter = Counter(b["norm"] for b in idx.blocks)
        total = 0
        for i, b in enumerate(idx.blocks):
            b["dup"] = dup[b["norm"]]
            total += b["len"]
            for t, tf in b["tf"].items():
                df[t] += 1
                idx.postings[t].append((i, tf))
        n = max(1, len(idx.blocks))
        idx.avg_len = total / n if total else 1.0
        for t, d in df.items():
            idx.idf[t] = max(0.01, math.log(1 + (n - d + 0.5) / (d + 0.5)))

    # ---------------------------------------------------------------- selection
    def _select(self, as_of):
        if not self.snaps:
            return None, "empty"
        if not as_of:
            return self.snaps[-1], None
        day = str(as_of)[:10]
        chosen = None
        for snap in self.snaps:
            if snap.taken_on <= day:
                chosen = snap
        if chosen is None:
            return None, "before"
        return chosen, ("after" if day > self.snaps[-1].taken_on else None)

    def _ghost(self, snap: SnapIndex) -> Optional[SnapIndex]:
        """Pages seen in earlier snapshots but absent from ``snap`` (last-seen versions)."""
        pages = self._ghost_pages.get(snap.snapshot_id) or {}
        if not pages:
            return None
        if snap.snapshot_id not in self._ghost_index:
            gidx = SnapIndex(snap.snapshot_id, snap.taken_on)
            for order, (sid, _taken, page) in enumerate(pages.values()):
                self._ingest_page(gidx, page, order)
                for r in gidx.rows[len(gidx.rows) - 1 :: -1]:
                    pass
            # stamp the originating snapshot on rows/blocks/pages
            seen_ids = {u: sid for u, (sid, _t, _p) in pages.items()}
            for r in gidx.rows:
                r.snapshot_id = seen_ids.get(r.url, snap.snapshot_id)
            for b in gidx.blocks:
                b["snapshot_id"] = seen_ids.get(b["url"], snap.snapshot_id)
            for p in gidx.pages:
                p["snapshot_id"] = seen_ids.get(p["url"], snap.snapshot_id)
            self._finish_index(gidx)
            self._ghost_index[snap.snapshot_id] = gidx
        return self._ghost_index[snap.snapshot_id]

    # ------------------------------------------------------------------- answer
    def answer(self, question, as_of=None):
        snap, note = self._select(as_of)
        if snap is None:
            if note == "before":
                return self._refuse(
                    "I don't know. The available website snapshots begin after that date, "
                    "so I can't say what the site showed then. Please contact us."
                )
            return self._refuse("I don't know. No website content has been loaded. Please contact us.")
        raw = question or ""
        q = _expand_question(raw)
        ans, cov = self._answer_from(snap, q, raw, as_of)
        if ans is None or not ans.confident or cov < 1.0:
            ghost = self._ghost(snap)
            if ghost is not None:
                g, gcov = self._answer_from(ghost, q, raw, as_of)
                if g is not None and g.confident and (ans is None or not ans.confident or gcov > cov):
                    revoked = self._revocation(snap, q)
                    if revoked is not None:
                        # The current snapshot says the product is gone: report that, and keep the
                        # last observed value apart from the current status.
                        blk, sentence, phrase = revoked
                        ans = Answer(
                            text=(f"I can't give a current value: the website states that the {phrase} "
                                  "has been discontinued or is no longer offered."),
                            sources=[self._src(blk.get("snapshot_id", snap.snapshot_id), blk["url"], sentence)],
                            confident=False,
                            conflict_note=(f"Last published before the page was removed: {g.text} "
                                           f"(sources: {', '.join(f'{s.url} @ {s.snapshot_id}' for s in g.sources)})")[:600],
                        )
                    else:
                        g.text += (
                            " Note: this comes from a page that is no longer on the website; "
                            "it was last seen in an earlier snapshot."
                        )
                        ans = g
        if ans is None:
            ans = self._refuse(DEFAULT_REFUSAL)
        if note == "after" and ans.confident:
            ans.text += " This reflects the most recent website snapshot available, which is earlier than the requested date."
        return ans

    def _answer_from(self, snap: SnapIndex, q: str, raw: str, as_of) -> tuple:
        """Route the question; returns (answer or None, coverage of the question by the evidence)."""
        ref_day = str(as_of)[:10] if as_of else snap.taken_on  # the day facts must be in force
        if _is_hours_question(q):
            r = self._answer_hours(snap, q)
            if r is not None:
                return r, 1.0
        if re.search(r"\brouting\b|\baba\b", q):
            return self._answer_identifier(snap, q), 1.0
        if re.search(r"\bhow many\b", q):
            r = self._answer_count(snap, q, as_of)
            if r is not None:
                return r, 1.0
        if re.search(r"\baddress\b|\blocated\b|\bwhere is\b|\bwhere's\b", q) and re.search(r"branch|office|location", q):
            r = self._answer_address(snap, raw)
            if r is not None:
                return r, 1.0
        if _is_link_question(q):
            r = self._answer_link(snap, q)
            if r is not None:
                return r, 1.0
        if re.search(r"\b(do|does|is|are)\s+(any|there|all)\b", q) and PCT_RE.search(q):
            r = self._answer_rate_yesno(snap, q, ref_day, raw)
            if r is not None:
                return r, 1.0
        if self._is_rate_question(q):
            r = self._answer_rates(snap, q, ref_day, raw)
            return (r, 1.0) if r is not None else (self._refuse(_refuse_line(q)), 0.0)
        r, cov = self._answer_passage(snap, q)
        return (r, cov) if r is not None else (self._refuse(DEFAULT_REFUSAL), 0.0)

    def _is_rate_question(self, q: str) -> bool:
        if re.search(r"\b(how many|who\b|where\b|when\b|address|hours|routing|eligible|url|link|website)\b", q):
            return False
        return bool(re.search(r"\b(apys?|aprs?|dividends?|interest rates?|rates?)\b", q))

    def _refuse(self, text: str) -> Answer:
        return Answer(text=text, sources=[], confident=False)

    def _src(self, snap_id: str, url: str, quote: Optional[str]) -> Source:
        return Source(url=url, snapshot_id=snap_id, quote=(quote or "")[:240] or None)

    # ------------------------------------------------------------------- rates
    def _filter_rows(self, snap: SnapIndex, q: str, ref_day: str = "", raw: str = "") -> tuple[list[Row], dict]:
        family = _wants_product(q)
        unknown = self._unknown_entities(snap, q, raw, family)
        if unknown:
            return [], {"family": family, "unknown": unknown, "scheduled": [], "overview": False, "flags": {}, "hard": False}
        months, years = _question_terms(q)
        model_years = _model_years(q)
        balance_q = _balance_query(q)
        qtoks = set(_content(q, self.stop))
        flags = {
            "jumbo": bool(re.search(r"\bjumbo\b", q)),
            "special": bool(re.search(r"\bspecial\b|\bpromo", q)),
            "business": bool(re.search(r"\bbusiness\b|\bcommercial\b", q)),
            "ira": bool(re.search(r"\bira\b|\broth\b|\btraditional\b|\bretirement\b", q)),
            "standard": bool(re.search(r"\bstandard\b|\bongoing\b|\bnon-?intro", q)),
            "intro": bool(re.search(r"\bintro", q)),
            "highest": bool(re.search(r"\bhighest\b|\bbest\b|\btop\b|\bmaximum\b|\bmost\b", q)),
            "lowest": bool(re.search(r"\blowest\b|\bminimum\b|\bcheapest\b", q)),
            "refi": bool(re.search(r"refinanc|\brefi\b", q)),
        }
        overview = not (months or years or model_years or balance_q or flags["highest"] or flags["lowest"]) and bool(
            re.search(r"\b(rates|aprs|apys)\b|\boffer\b|\ball\b|\blist\b|\bevery\b|\boverview\b|certificate of account|what .* do you have", q)
        )
        must = [pat for trig, pat in MUST_MATCH_QUALIFIERS if re.search(trig, q)]
        for m in re.finditer(r"\b(\d+\s*/\s*\d+)\b", q):
            must.append(re.escape(re.sub(r"\s+", "", m.group(1))))
        product_tokens = {t for t in qtoks if t in snap.vocab and t not in RATE_WORDS and t not in GENERIC_NAME_WORDS}
        if family:
            product_tokens -= FAMILY_WORDS.get(family, set())

        cands = []
        scheduled = []
        for r in snap.rows:
            if r.expired or (r.prose and r.priority < 2):
                continue
            kinds = {k for k, _p, _d, h in r.measures if k in RATE_KINDS}
            if not kinds:
                continue
            if r.is_business != flags["business"] and not (overview and family == "card" and r.is_business):
                continue
            if r.is_ira != flags["ira"] and not (family == "certificate" and flags["ira"] and r.is_ira):
                if not (flags["ira"] and r.is_ira):
                    continue
            if flags["jumbo"] and not r.is_jumbo:
                continue
            if flags["standard"] and r.is_intro:
                continue
            if flags["intro"] and not (r.is_intro or any(_is_intro_header(h) for _k, _p, _d, h in r.measures)):
                continue
            if family and not _family_match(family, r):
                continue
            if months and not (r.term_months & months):
                continue
            if years and not (r.term_years & years):
                continue
            if model_years:
                if not r.year_span or not any(r.year_span[0] <= y <= r.year_span[1] for y in model_years):
                    continue
            if balance_q:
                if not r.balance or not _overlaps(balance_q, r.balance):
                    continue
            if must and not all(re.search(p, r.text) for p in must):
                continue
            if not _in_effect(r, ref_day or snap.taken_on):
                scheduled.append(r)  # published, but its effective date is still ahead of ref_day
                continue
            cands.append(r)
        info = {"family": family, "overview": overview, "flags": flags, "hard": bool(months or years or model_years or balance_q),
                "unknown": [], "scheduled": scheduled}
        if not cands:
            return [], info
        if product_tokens:
            narrowed = [r for r in cands if product_tokens <= r.name_tokens]
            if narrowed:
                cands = narrowed
            elif not family:
                cands = [r for r in cands if product_tokens & r.name_tokens] or cands
        elif not family and not info["hard"]:
            cands = [r for r in cands if qtoks & set(_tokens(r.text))]
        if not cands:
            return [], info
        if flags["special"] and family == "certificate":
            cands = [r for r in cands if r.is_special] or cands
        elif family == "certificate" and (months or years) and not flags["special"]:
            regular = [r for r in cands if not r.is_special]
            cands = regular or cands
        if family == "mortgage" and not flags["refi"]:
            purchase = [r for r in cands if not re.search(r"refinanc", r.text)]
            cands = purchase or cands
        elif family == "mortgage" and flags["refi"]:
            cands = [r for r in cands if re.search(r"refinanc", r.text)] or cands
        best_p = max(r.priority for r in cands)
        if best_p >= 2:
            cands = [r for r in cands if r.priority >= 2]
        tabled = {r.name_tokens for r in cands if r.kind == "table"}
        cands = [r for r in cands if not (r.prose and r.name_tokens in tabled)]
        return cands, info

    def _resolve(self, rows: list) -> list:
        """Group rows that state the same fact and pick the authoritative row of each group.
        Shared by ordinary rate answers and yes/no questions so both report one truth."""
        groups: dict = {}
        order: list = []
        for r in rows:
            key = (r.family, r.name_tokens, tuple(_norm(x) for x in r.quals), tuple(sorted(r.term_months)),
                   tuple(sorted(r.term_years)), r.year_span, r.balance and (round(r.balance[0], 2), round(min(r.balance[1], 1e12), 2)),
                   r.is_intro, _norm(r.label) if _is_qualifier_text(r.label) else "")
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)
        out = []
        for key in order:
            members = sorted(groups[key], key=_authority, reverse=True)
            primary = members[0]
            pvals = _values_of(primary)
            agree = [m for m in members if _values_of(m) == pvals]
            disagree = [m for m in members if _values_of(m) != pvals and m.url != primary.url]
            out.append((primary, agree, disagree))
        return out

    def _unknown_entities(self, snap: SnapIndex, q: str, raw: str, family) -> list:
        """Words in a rate question that look like a product name the site does not have.
        A word counts when it appears nowhere in the snapshot, or when it is capitalised and
        directly precedes a product word ("Gamma certificate") without being a known product."""
        known = snap.vocab | set(snap.idf)
        product_words = set().union(*FAMILY_WORDS.values()) | GENERIC_NAME_WORDS | RATE_WORDS
        raw_words = re.findall(r"[A-Za-z][A-Za-z0-9$'\-]*", raw or "")
        capitalised = set()
        for i, w in enumerate(raw_words):
            if i == 0 or not w[:1].isupper():
                continue
            following = [x.lower() for x in raw_words[i + 1: i + 3]]
            if any(_stem(x) in product_words or x in product_words for x in following):
                capitalised.update(_content(w, self.stop))
        before_product = set()
        for i, w in enumerate(raw_words):
            following = [x.lower() for x in raw_words[i + 1: i + 3]]
            if any(_stem(x) in product_words or x in product_words for x in following):
                before_product.update(_content(w, self.stop))
        skip = (RATE_WORDS | GENERIC_NAME_WORDS | ENTITY_SKIP | INSTITUTION_WORDS | set().union(*FAMILY_WORDS.values())
                | set(MONTHS) | {"quarter", "spring", "summer", "fall", "autumn", "winter", "early", "late", "mid"})
        out = []
        for t in _content(q, self.stop):
            if t.isdigit() or t in skip or t in snap.vocab or t not in before_product:
                continue
            if t not in known or t in capitalised:
                out.append(t)
        # echo the question's own spelling, never a stemmed token
        words = []
        for t in out:
            w = next((w for w in raw_words if _stem(w.lower().strip("'")) == t or w.lower() == t), t)
            if w not in words:
                words.append(w)
        return words

    def _answer_rates(self, snap: SnapIndex, q: str, ref_day: str = "", raw: str = "") -> Optional[Answer]:
        rows, info = self._filter_rows(snap, q, ref_day, raw)
        if info.get("unknown"):
            names = " ".join(info["unknown"])
            return self._refuse(f"I don't know. I can't find anything called '{names}' on the website. Please contact us.")
        if not rows:
            if info.get("scheduled"):
                return self._scheduled_answer(info["scheduled"], q, ref_day or snap.taken_on)
            return None
        flags = info["flags"]
        resolved = self._resolve(rows)
        notes = []
        for primary, agree, disagree in resolved:
            if disagree:
                other = disagree[0]
                notes.append(
                    f"{other.url} shows {', '.join(_values_of(other))} for {primary.display_name()}; "
                    f"using {', '.join(_values_of(primary))} from {primary.url} ({_describe_page(primary.url, primary.priority)}"
                    f"{', effective ' + primary.effective if primary.effective else ''})."
                )
        for primary, _agree, _dis in self._resolve(info.get("scheduled") or []):
            notes.append(
                f"A later value is published for {primary.display_name()}: {', '.join(_values_of(primary))} "
                f"takes effect {primary.effective} (not in effect on {ref_day or snap.taken_on})."
            )
        chosen = [(primary, agree) for primary, agree, _dis in resolved]
        if flags["highest"] or flags["lowest"]:
            chosen = _extreme(chosen, flags["highest"])
        chosen.sort(key=lambda pr: (-pr[0].priority, pr[0].order))
        lines = []
        sources: list[Source] = []
        cited = set()
        for primary, agree in chosen:
            piece = _format_row(primary, q)
            if not piece:
                continue
            lines.append(piece)
            for r in agree:
                if r.url not in cited and len(sources) < 8:
                    sources.append(self._src(r.snapshot_id, r.url, r.quote))
                    cited.add(r.url)
        if not lines:
            return None
        text = " ".join(lines)
        return Answer(text=text[:3000], sources=sources, confident=True, conflict_note=" ".join(notes)[:600] or None)

    def _scheduled_answer(self, rows: list, q: str, ref_day: str) -> Optional[Answer]:
        """Only future-dated tables match: describe the scheduled values without asserting them."""
        parts = []
        sources = []
        cited = set()
        for primary, agree, _dis in self._resolve(rows):
            piece = _format_row(primary, q).rstrip(".")
            if not piece:
                continue
            parts.append(f"{piece} takes effect {primary.effective}")
            for r in agree:
                if r.url not in cited and len(sources) < 6:
                    sources.append(self._src(r.snapshot_id, r.url, r.quote))
                    cited.add(r.url)
        if not parts:
            return None
        text = (f"Not yet in effect as of {ref_day}: " + "; ".join(parts)
                + ". No rate currently in effect is listed for this product.")
        return Answer(text=text[:3000], sources=sources, confident=False)

    def _answer_rate_yesno(self, snap: SnapIndex, q: str, ref_day: str = "", raw: str = "") -> Optional[Answer]:
        m = PCT_RE.search(q)
        asked = round(float(m.group(1)), 4) if m else None
        if asked is None:
            return None
        want_kind = "apy" if re.search(r"\bapy\b", q) else "apr" if re.search(r"\bapr\b", q) else "dividend" if "dividend" in q else None
        q_stripped = PCT_RE.sub(" ", q)
        rows, info = self._filter_rows(snap, q_stripped, ref_day, raw)
        if info.get("unknown") or not rows:
            return None  # the ordinary rate path explains the gap
        hits = []
        tops: dict = {}
        want_intro = bool(re.search(r"\bintro|\bpromo", q))
        for primary, _agree, _dis in self._resolve(rows):  # resolved values only: one truth for every path
            for kind, parsed, disp, header in primary.measures:
                if kind not in RATE_KINDS or (want_kind and kind != want_kind):
                    continue
                if _is_intro_header(header) != want_intro:
                    continue
                vals = [parsed[1]] if parsed[0] == "point" else [parsed[1], parsed[2]]
                if any(abs(v - asked) < 1e-6 for v in vals):
                    hits.append((primary, kind, disp))
                if parsed[0] == "point":
                    cur = tops.get(primary.name_tokens)
                    if cur is None or parsed[1] > cur[1]:
                        tops[primary.name_tokens] = (primary, parsed[1], kind, disp)
        kind_name = (want_kind or "rate").upper().replace("DIVIDEND", "dividend rate")
        if hits:
            r, kind, disp = hits[0]
            text = f"Yes. {r.display_name()} pays {disp} {_kind_label(kind)}."
            return Answer(text=text, sources=[self._src(r.snapshot_id, r.url, r.quote)], confident=True)
        parts = []
        sources = []
        for r, v, kind, disp in sorted(tops.values(), key=lambda t: (-t[0].priority, t[0].order)):
            parts.append(f"{r.display_name()}: {disp} {_kind_label(kind)}")
            if len(sources) < 6:
                sources.append(self._src(r.snapshot_id, r.url, r.quote))
        if not parts:
            return None
        text = f"No. No listed tier pays {asked:g}% {kind_name}. The highest listed tiers are: " + "; ".join(parts) + "."
        return Answer(text=text[:3000], sources=sources, confident=True)

    # ---------------------------------------------------------------- passages
    def _search_blocks(self, snap: SnapIndex, q_tokens: list[str], k: int = 40):
        scores: dict = defaultdict(float)
        matched: dict = defaultdict(set)
        body_hit: dict = defaultdict(set)
        k1, b = 1.2, 0.4
        distinct = set(q_tokens)
        q_family = _family_of(" ".join(q_tokens))
        weights = {t: (1.0, t) for t in sorted(distinct)}
        for t in sorted(distinct):  # fixed order so a shared synonym always maps to the same origin
            for syn in SYNONYMS.get(t, []):
                weights.setdefault(_stem(syn), (0.5, t))
        for t, (w, origin) in sorted(weights.items()):  # fixed order: scores must not depend on hashing
            idf = snap.idf.get(t)
            if not idf:
                continue
            for i, tf in snap.postings.get(t, []):
                blk = snap.blocks[i]
                denom = tf + k1 * (1 - b + b * blk["len"] / snap.avg_len)
                scores[i] += w * idf * (tf * (k1 + 1) / denom)
                matched[i].add(origin)
                if t in blk["body"]:
                    body_hit[i].add(origin)
        ranked = []
        for i, s in scores.items():
            blk = snap.blocks[i]
            if blk["dup"] >= 4:
                s *= 0.5
            s *= 1 + 0.2 * (blk["priority"] - 1)  # product/rate pages over articles and promos
            s *= 0.5 + 0.5 * len(matched[i]) / max(1, len(distinct))
            head_fam = _family_of(blk["heading"])
            if head_fam != "other" and head_fam != q_family:  # section about a product the question did not ask about
                s *= 0.6
            s *= 1 + 0.08 * min(3, len(distinct & blk.get("title_toks", set())))
            ranked.append((s, i, matched[i], body_hit[i]))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        return ranked[:k]

    def _answer_passage(self, snap: SnapIndex, q: str) -> tuple:
        q_tokens = [t for t in _content(q, self.stop)]
        if not q_tokens:
            return None, 0.0
        ranked = self._search_blocks(snap, q_tokens)
        if not ranked:
            return None, 0.0
        if re.search(r"\bfees?\b|\bcosts?\b|\bprices?\b|\bhow much\b|\bcharges?\b", q):
            ranked = [x for x in ranked if re.search(r"\$\s?\d|\d\s?%", snap.blocks[x[1]]["text"])]
            if not ranked:
                return None, 0.0
        best_s = ranked[0][0]
        distinct = set(q_tokens)
        need = 2 if len(distinct) >= 2 else 1
        good = [x for x in ranked if x[0] >= 0.9 * best_s and len(x[2]) >= need]
        if not good:
            top = ranked[0]
            if len(top[2]) < need or top[0] < 1.0:
                return None, 0.0
            good = [top]
        good.sort(key=lambda x: (-x[0], x[1]))
        if snap.blocks[good[0][1]]["pub"]:  # dated articles: prefer the most recent among near-equal matches
            dated = [x for x in good if snap.blocks[x[1]]["pub"]]
            dated.sort(key=lambda x: (snap.blocks[x[1]]["pub"], snap.blocks[x[1]]["fetched_at"], x[0], -x[1]), reverse=True)
            good = dated + [x for x in good if not snap.blocks[x[1]]["pub"]]
        s0, i0, m0, b0 = good[0]
        blk = snap.blocks[i0]
        if not blk.get("lead"):  # an article's lead paragraph beats a pointer deeper in the same page
            for s, i, m, bh in ranked:
                cand = snap.blocks[i]
                if cand["url"] == blk["url"] and cand.get("lead") and s >= 0.6 * best_s and len(bh) >= max(1, len(b0)) and len(m) >= need:
                    blk, m0 = cand, m
                    break
        confident = True
        if ELIGIBILITY_Q_RE.search(q):  # an eligibility answer must state criteria, not an invitation
            def states_criteria(b):
                return bool(ELIGIBILITY_EVIDENCE_RE.search(f"{b['heading']} {b['text']}"))
            if not states_criteria(blk):
                alt = next((snap.blocks[i] for s, i, m, bh in ranked
                            if s >= 0.5 * best_s and len(m) >= need and states_criteria(snap.blocks[i])), None)
                if alt is not None:
                    blk = alt
                    m0 = next(m for s, i, m, bh in ranked if snap.blocks[i] is alt)
                else:
                    confident = False
        coverage = len(m0) / max(1, len(distinct))
        if re.search(r"\bhow (?:can|do|would|could|should) i\b|\bhow to\b", q) and coverage < 0.8:
            return self._contact_fallback(snap), 0.0
        if re.search(r"\bfees?\b|\bcosts?\b|\bprices?\b|\bhow much\b|\bcharges?\b", q) and coverage < 0.99:
            return None, 0.0
        text = _compose_block(blk)
        sid = blk.get("snapshot_id", snap.snapshot_id)
        return Answer(text=text, sources=[self._src(sid, blk["url"], blk["text"])], confident=confident), coverage

    def _contact_fallback(self, snap: SnapIndex) -> Optional[Answer]:
        """Procedural question the pages do not answer: point to the site's own contact channel."""
        pages = [p for p in snap.pages if re.search(r"/contact(?:-us)?/?$", urlparse(p["url"]).path) or "contact" in p["title"].lower()]
        if not pages:
            return None
        page = max(pages, key=lambda p: p["priority"])
        phones = Counter(re.findall(r"\b\d{3}-\d{3}-\d{4}\b", page["text"]))
        if not phones:
            return None
        phone = phones.most_common(1)[0][0]
        line = next((l for l in page["text"].splitlines() if phone in l), phone)
        text = (f"I don't know how to do that from the website alone. Please contact us at {phone} "
                f"or through the contact page ({page['url']}).")
        return Answer(text=text, sources=[self._src(page.get("snapshot_id", snap.snapshot_id), page["url"], _clean_inline(line))], confident=False)

    def _revocation(self, snap: SnapIndex, q: str):
        """Does the selected snapshot say the product named in the question was withdrawn?
        Returns (block, sentence, phrase) or None.  Every product word of the question must
        appear in the sentence that carries the withdrawal wording."""
        skip = RATE_WORDS | GENERIC_NAME_WORDS | ENTITY_SKIP | INSTITUTION_WORDS
        ents = {t for t in _content(q, self.stop) if not t.isdigit() and t not in skip}
        if not ents:
            return None
        best = None
        for blk in snap.blocks:
            if not (ents <= (blk["body"] | set(_content(blk["heading"], self.stop)))):
                continue
            if not REVOKED_RE.search(blk["text"]):
                continue
            for sent in re.split(r"(?<=[.!?])\s+", blk["text"]):
                if REVOKED_RE.search(sent) and ents <= set(_content(sent, self.stop)):
                    cand = (blk["pub"], blk)
                    if best is None or cand[0] > best[0][0]:
                        best = (cand, sent.strip())
                    break
        if best is None:
            return None
        (_pub, blk), sent = best
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9$'\-]*", q) if _stem(w.lower()) in ents or w.lower() in ents]
        phrase = " ".join(words) if words else "product"
        return blk, sent, phrase

    # --------------------------------------------------------------- extractors
    def _answer_identifier(self, snap: SnapIndex, q: str) -> Answer:
        found: Counter = Counter()
        where: dict = defaultdict(list)
        for page in snap.pages:
            clean = _clean_inline(page["text"])
            for m in re.finditer(r"routing(?: number| no\.?)?[^0-9\n]{0,40}?(\d{9})\b|\b(\d{9})\b[^.\n]{0,40}routing", clean, re.I):
                num = m.group(1) or m.group(2)
                found[num] += 1
                where[num].append((page, clean[max(0, m.start() - 60): m.end() + 40]))
        if not found:
            return self._refuse("I don't know. A routing number isn't listed on the website. Please contact us.")
        number = found.most_common(1)[0][0]
        pages = sorted(where[number], key=lambda pq: -pq[0]["priority"])
        sources = []
        cited = set()
        for page, quote in pages:
            if page["url"] in cited:
                continue
            cited.add(page["url"])
            sources.append(self._src(page.get("snapshot_id", snap.snapshot_id), page["url"], quote))
            if len(sources) >= 3:
                break
        return Answer(text=f"The routing number is {number}.", sources=sources, confident=True)

    def _answer_count(self, snap: SnapIndex, q: str, as_of) -> Optional[Answer]:
        m = re.search(r"how many ((?:[a-z0-9'\-]+\s){0,3}?[a-z0-9'\-]+?)(?=\s+(?:does|do|did|are|were|is|was|has|have|used|use|work|worked|took|serve|operate|operates)\b|\?|$|\s+in\b|\s+last\b|\s+this\b)", q)
        if not m:
            return None
        phrase = m.group(1)
        noun_toks = [t for t in _content(phrase, self.stop)]
        if not noun_toks:
            return None
        noun = noun_toks[-1]
        others = set(_content(q, self.stop)) - set(noun_toks) - {"last", "year", "many", "how"} - INSTITUTION_WORDS
        others = {t for t in others if not t.isdigit()}
        ref_year = int((str(as_of)[:4] if as_of else snap.taken_on[:4]) or snap.taken_on[:4])
        target = None
        ym = re.search(r"\b(20\d\d)\b", q)
        if ym and "model" not in q:
            target = int(ym.group(1))
        elif re.search(r"\blast year\b|\bprevious year\b", q):
            target = ref_year - 1
        elif re.search(r"\bthis year\b", q):
            target = ref_year
        hits = []
        for blk in snap.blocks:
            if noun not in blk["tf"]:
                continue
            if others and not (others & set(blk["tf"])):
                continue
            for sent in re.split(r"(?<=[.!?])\s+", blk["text"]):
                words = sent.split()
                for wi, w in enumerate(words):
                    num = re.fullmatch(r"\$?(\d{1,3}(?:,\d{3})+|\d+)\b[,.]?", w)
                    if not num or w.startswith("$"):
                        continue
                    window = words[wi + 1: wi + 4]
                    pos = next((k for k, ww in enumerate(window) if any(_stem(t) == noun for t in _tokens(ww))), None)
                    if pos is None or any(_tokens(ww) and _tokens(ww)[0] in COUNT_BREAKERS for ww in window[:pos]):
                        continue
                    sent_toks = set(_content(sent, self.stop))
                    if others and len(others & sent_toks) < max(1, math.ceil(0.5 * len(others))):
                        continue
                    value = int(num.group(1).replace(",", ""))
                    if 1900 <= value <= 2100 and len(num.group(1)) == 4:
                        continue
                    yrs = [int(y) for y in re.findall(r"\b(20\d\d)\b", sent) if int(y) != value]
                    fact_year = max(yrs) if yrs else (int(blk["pub"][:4]) if blk["pub"] else None)
                    adjacent = {t for ww in words[max(0, wi - 2): wi] for t in _tokens(ww)}
                    prefix = {t for ww in words[:wi] for t in _tokens(ww)}
                    asserted = bool(adjacent & COUNT_QUANTITY_WORDS) or bool(prefix & COUNT_VERB_WORDS)
                    # a stated quantity ("has 14 branches") beats narrative; a bare noun right after the
                    # number ("14 branches") beats a modified one ("5,000 participating branches")
                    rank = 2 * int(asserted) + int(pos == 0)
                    hits.append({"value": value, "sentence": sent.strip(), "blk": blk, "pub": blk["pub"], "year": fact_year, "rank": rank})
                    break
        if not hits:
            return None
        top_rank = max(h["rank"] for h in hits)
        hits = [h for h in hits if h["rank"] == top_rank]
        seen = set()
        uniq = []
        for h in sorted(hits, key=lambda h: (h["pub"], h["blk"]["fetched_at"]), reverse=True):
            key = (h["value"], h["year"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(h)
        if target is not None:
            exact = [h for h in uniq if h["year"] == target]
            if exact:
                return self._count_answer(snap, exact[:2], "")
            intro = f"No figure for {target} is published on the website. The most recent published figures are: "
            return self._count_answer(snap, uniq[:3], intro)
        newest_pub = uniq[0]["pub"]
        current = [h for h in uniq if h["pub"] == newest_pub][:2]
        return self._count_answer(snap, current, "")

    def _count_answer(self, snap, hits, intro) -> Answer:
        parts = []
        sources = []
        for h in hits:
            sent = _trim_sentence(h["sentence"], 220)
            when = f" (published {h['pub']})" if h["pub"] else ""
            parts.append(f"{sent}{when}")
            sources.append(self._src(h["blk"].get("snapshot_id", snap.snapshot_id), h["blk"]["url"], h["sentence"]))
        return Answer(text=(intro + " ".join(parts))[:1500], sources=sources[:4], confident=True)

    def _answer_address(self, snap: SnapIndex, raw: str) -> Optional[Answer]:
        m = (re.search(r"(?:address|location)\s+(?:of|for)\s+(?:the\s+|your\s+)?(.+?)\s+branch", raw, re.I)
             or re.search(r"(?:where is|where's|located)\s+(?:the\s+|your\s+)?(.+?)\s+branch", raw, re.I)
             or re.search(r"(?:the\s+)?([A-Z][A-Za-z0-9.' -]+?)\s+branch(?:'s)?\s+(?:address|location)", raw))
        if not m:
            return None
        name = m.group(1).strip()
        toks = [t for t in _tokens(name) if t not in self.stop and t not in {"branch", "office", "location"}]
        if not toks:
            return None
        street = re.compile(r"\d{1,6}\s+[A-Za-z0-9.' -]+,\s*[A-Za-z .]+,\s*[A-Z]{2},?\s*\d{5}")
        best = None
        for page in sorted(snap.pages, key=lambda p: -p["priority"]):
            lines = page["text"].splitlines()
            for li, line in enumerate(lines):
                if not street.search(line) or len(line) > 160:
                    continue
                ctx = " ".join(lines[max(0, li - 3): li + 1]).lower()
                if all(t in _tokens(ctx) for t in toks):
                    best = (page, line.strip())
                    break
            if best:
                break
        if not best:
            return self._refuse(f"I don't know. An address for the {name} branch isn't listed on the website. Please contact us.")
        page, line = best
        return Answer(text=f"The {name} branch is at {line}.", sources=[self._src(page.get("snapshot_id", snap.snapshot_id), page["url"], line)], confident=True)

    def _answer_link(self, snap: SnapIndex, q: str) -> Optional[Answer]:
        ranked = self._rank_links(snap, q)
        if not ranked:
            return None
        top_key, top = ranked[0]
        if top["score"] < 2.0:
            return None
        text = f"{top_key}"
        sources = []
        dest = next((p for p in snap.pages if _url_path_key(p["url"]) == _url_path_key(top_key)), None)
        if dest is not None:
            sources.append(self._src(dest.get("snapshot_id", snap.snapshot_id), dest["url"], _clean_inline(dest["text"])[:200]))
        sources.append(self._src(snap.snapshot_id, top["url"], top["line"]))
        if len(ranked) > 1:
            k2, s2 = ranked[1]
            if s2["score"] >= 0.7 * top["score"] and _url_path_key(k2) != _url_path_key(top_key):
                text += f" (see also {k2})"
                sources.append(self._src(snap.snapshot_id, s2["url"], s2["line"]))
        return Answer(text=text, sources=sources, confident=True)

    def _rank_links(self, snap: SnapIndex, q: str) -> list:
        q_tokens = {t for t in _content(q, self.stop) if t not in {"link", "url", "website", "web", "address", "page", "online", "where"}}
        if not q_tokens:
            return []
        best: dict = {}
        page_keys = {_url_path_key(p["url"]): p for p in snap.pages}
        for anchor, href, ctx, url, line in snap.links:
            if href.startswith(("mailto:", "tel:")) or href.rstrip("#").endswith("/#") or href.endswith("#"):
                continue
            if re.search(r"\.(?:jpe?g|png|gif|svg|webp|css|js)(?:\?|$)", href, re.I):
                continue
            key = _normalize_href(href)
            if not key:
                continue
            a_toks = set(_content(anchor, self.stop))
            anchor_w = 3.0 if len(_tokens(anchor)) <= 7 else 1.0
            h_toks = set(_content(urlparse(href).path.replace("-", " ").replace("/", " ") + " " + (urlparse(href).fragment or "").replace("-", " "), self.stop))
            host_toks = set(_content(urlparse(href).netloc.replace(".", " "), set()))
            c_toks = set(_content(ctx, self.stop))
            score = anchor_w * len(q_tokens & a_toks) + 1.0 * len(q_tokens & h_toks) + 1.0 * len(q_tokens & host_toks) + 1.0 * len(q_tokens & c_toks)
            if score <= 0:
                continue
            if self._same_site(href):
                score += 0.3
            dest = page_keys.get(_url_path_key(href))
            if dest is not None:  # the destination is a page that exists in this snapshot
                score += 1.0 + 0.5 * (dest["priority"] - 1)
            if ("business" in urlparse(href).path.lower()) != bool(re.search(r"\bbusiness\b|\bcommercial\b", q)):
                score -= 2.0
            cur = best.get(key)
            if cur is None:
                best[key] = {"score": score, "count": 1, "anchor": anchor, "url": url, "href": href, "line": line}
            else:
                cur["count"] += 1
                if score > cur["score"]:
                    cur.update(score=score, anchor=anchor, url=url, href=href, line=line)
        for v in best.values():
            v["score"] += 0.15 * math.log(v["count"] + 1)
        return sorted(best.items(), key=lambda kv: -kv[1]["score"])

    def _schedules_for(self, snap: SnapIndex) -> list[dict]:
        if snap.schedules is not None:
            return snap.schedules
        out = []
        for page in snap.pages:
            lines = page["text"].splitlines()
            for li, line in enumerate(lines):
                s = line.strip()
                sm = SERVICE_RE.match(s)
                if not sm:
                    continue
                service = sm.group(1).lower()
                service = "drive-up" if service.startswith("drive") else service
                entries = []
                rest = sm.group(2).strip()
                if rest and (DAY_RE.match(rest) or TIME_RE.search(rest)):
                    entries.append(_clean_inline(rest))
                j = li + 1
                blanks = 0
                while j < len(lines) and len(entries) < 10:
                    t = lines[j].strip()
                    if not t:
                        blanks += 1
                        if blanks > 2:
                            break
                        j += 1
                        continue
                    blanks = 0
                    if SERVICE_RE.match(t) or t.startswith(("#", "|")):
                        break
                    if DAY_RE.match(t) and (TIME_RE.search(t) or re.search(r"closed|atm only|appointment", t, re.I)):
                        entries.append(_clean_inline(t))
                        j += 1
                        continue
                    break
                if not entries:
                    continue
                before = " ".join(lines[max(0, li - 6): li])
                ctx = f"{page['title']} {page['text'][:600]} {before}"
                out.append({
                    "service": service, "entries": entries, "page": page, "pub": page["pub"],
                    "ctx_tokens": set(_tokens(ctx)), "place": page["title"].split("|")[0].strip(),
                    "norm": (service, tuple(_norm(e) for e in entries)),
                    "quote": _clean_inline(" ".join(x.strip() for x in lines[li:j] if x.strip()))[:240],
                })
        snap.schedules = out
        return out

    def _answer_hours(self, snap: SnapIndex, q: str) -> Optional[Answer]:
        schedules = self._schedules_for(snap)
        branch = _named_branch(q)
        want = set()
        if re.search(r"drive", q):
            want.add("drive-up")
        if re.search(r"lobby", q):
            want.add("lobby")
        if re.search(r"\batm\b", q):
            want.add("atm")
        if not want:
            want = {"lobby", "drive-up"}
        if branch:
            btoks = [t for t in _tokens(branch) if t not in self.stop and t not in {"branch", "office", "location", "street", "st", "avenue", "ave", "road", "rd"}]
            if btoks:
                matched = [s for s in schedules if all(t in s["ctx_tokens"] for t in btoks)]
                if not matched:
                    return self._refuse(f"I don't know. Hours for the {branch} branch aren't listed on the website. Please contact us.")
                schedules = matched
        if not schedules:
            return None
        chosen = [s for s in schedules if s["service"] in want]
        if not chosen:
            return None
        chosen.sort(key=lambda s: (s["pub"], s["page"]["fetched_at"]), reverse=True)
        seen = set()
        parts = []
        sources = []
        cited = set()
        for s in chosen:
            if s["norm"] in seen:
                continue
            seen.add(s["norm"])
            when = f", {s['pub']}" if s["pub"] else ""
            parts.append(f"{s['place']}{when}: {s['service'].title()} {'; '.join(s['entries'])}.")
            if s["page"]["url"] not in cited and len(sources) < 6:
                cited.add(s["page"]["url"])
                sources.append(self._src(s["page"].get("snapshot_id", snap.snapshot_id), s["page"]["url"], s["quote"]))
            if sum(len(p) for p in parts) > 1100:
                break
        if not parts:
            return None
        if branch:
            text = f"Hours published for the {branch} branch: " + " ".join(parts)
        else:
            text = ("Hours are published per branch in the website's branch announcements and vary by location. "
                    + " ".join(parts) + " Tell me which branch you mean for that location's schedule.")
        return Answer(text=text[:2000], sources=sources, confident=True)

    # ---------------------------------------------------------------- conflicts
    def conflicts(self):
        found: dict = {}
        order: list = []
        for snap in self.snaps:  # later snapshots overwrite earlier instances of the same conflict
            for key, c in self._snapshot_conflicts(snap):
                if key not in found:
                    order.append(key)
                found[key] = c
        return [found[k] for k in order]

    def _snapshot_conflicts(self, snap: SnapIndex) -> list:
        groups: dict = defaultdict(list)
        for r in snap.rows:
            if r.prose or r.is_intro or not _in_effect(r, snap.taken_on):
                continue  # a future-dated table is a scheduled change, not a contradiction
            vals = {}
            for kind, parsed, _disp, header in r.measures:
                if kind not in RATE_KINDS or _is_intro_header(header):
                    continue
                if parsed[0] == "point" and parsed[1] == 0:
                    continue
                vals.setdefault(kind, parsed)
            if vals:
                groups[_conflict_key(r)].append((r, vals))
        out = []
        for key, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda rp: _authority(rp[0]), reverse=True)
            used: set = set()
            for a, (ra, va) in enumerate(items):
                if a in used:
                    continue
                partners = []
                for b in range(a + 1, len(items)):
                    if b in used:
                        continue
                    rb, vb = items[b]
                    if ra.url == rb.url and _norm(ra.product) == _norm(rb.product):
                        continue
                    if _subtype_compatible(ra, rb) and _tiers_compatible(ra, rb) and _measures_conflict(va, vb):
                        partners.append(b)
                if not partners:
                    continue
                used.add(a)
                used.update(partners)
                comp = [items[a]] + [items[b] for b in partners]
                order = ("rate", "dividend", "apr", "apy")
                shared = [k for k in order if sum(1 for _r, v in comp if k in v) >= 2]
                kinds = shared or sorted({k for _r, v in comp for k in v}, key=lambda k: order.index(k) if k in order else 9)
                values = []
                sources = []
                for r, v in comp:
                    tok = _measure_string(v, kinds)
                    if tok not in values:
                        values.append(tok)
                    if len(sources) < 4:
                        sources.append(Source(url=r.url, snapshot_id=r.snapshot_id, quote=r.quote))
                if len(values) < 2:
                    continue
                loser = next((r for r, v in comp[1:] if _measure_string(v, kinds) != values[0]), None)
                topic = f"{_topic_name(ra)} {'/'.join(_kind_label(k) for k in kinds)}".strip()[:180]
                identity = (key, ra.year_span, ra.balance and (round(ra.balance[0], 2), round(min(ra.balance[1], 1e12), 2)))
                out.append((identity, Conflict(
                    topic=topic, values=values[:6], sources=sources, resolution=values[0],
                    reason=_reason_for(ra, loser, snap),
                )))
        out.extend(self._count_conflicts(snap))
        return out

    def _count_conflicts(self, snap: SnapIndex) -> list[Conflict]:
        """Dated boilerplate counts (e.g. number of branches) that disagree within a snapshot."""
        hits = []
        for page in snap.pages:
            if not page["pub"]:
                continue
            clean = _clean_inline(page["text"])
            for m in re.finditer(r"(?<![\d,.$])\b(\d{1,3})\s+((?:[a-z-]+\s+){0,2}?)branch(?:es)?\b(?:\s+office)?(?:\s+locations?)?", clean, re.I):
                between = m.group(2).lower().split()
                if any(w in COUNT_BREAKERS for w in between):
                    continue
                sent_start = clean.rfind(".", 0, m.start()) + 1
                hits.append((page["pub"], m.group(1), page, clean[sent_start: m.end() + 80].strip()[:200]))
        vals = []
        for _d, n, _p, _s in hits:
            if n not in vals:
                vals.append(n)
        if len(vals) < 2:
            return []
        hits.sort(key=lambda h: h[0], reverse=True)
        winner = hits[0][1]
        sources = []
        seen = set()
        for pub, n, page, sent in hits:
            if n in seen:
                continue
            seen.add(n)
            sources.append(Source(url=page["url"], snapshot_id=snap.snapshot_id, quote=sent))
            if len(sources) >= 4:
                break
        return [(("count", "branches"), Conflict(
            topic="number of branches",
            values=[winner] + [v for v in vals if v != winner],
            sources=sources,
            resolution=winner,
            reason=(f"Pages published on different dates state different branch counts as current. "
                    f"The most recently published page ({hits[0][0]}) gives {winner}; older pages reflect the count at their publication date."),
        ))]


# --------------------------------------------------------------------------
# Module-level helpers used by the class
# --------------------------------------------------------------------------

def _split_table_row(line: str):
    s = (line or "").strip()
    if "|" not in s:
        return None
    cells = [c.strip() for c in s.strip("|").split("|")]
    if len(cells) < 2 and not s.startswith("|"):
        return None
    return cells


def _is_separator(cells: list[str]) -> bool:
    blob = "".join(cells)
    return bool(blob) and not re.search(r"[a-zA-Z0-9$%]", blob)


def _values_of(row: Row) -> tuple:
    return tuple(_value_token(p) for k, p, _d, _h in row.measures if k in RATE_KINDS)


def _in_effect(row: Row, day: str) -> bool:
    """Valid time: a table that says 'effective <date>' is in force only from that date.
    Without an effective date the observed value counts as in force."""
    return not row.effective or row.effective <= (day or "")


def _subtype_compatible(a: Row, b: Row) -> bool:
    """Two rows describe the same product only if their distinguishing names agree.
    Two tables must agree exactly ("Alpha" vs "Beta" are different products); a sentence
    may carry extra words ("Spring special: 6-month certificate") and is compared when one
    name set contains the other."""
    if a.subtype == b.subtype:
        return True
    if a.kind == "table" and b.kind == "table":
        return False
    return a.subtype <= b.subtype or b.subtype <= a.subtype


def _authority(row: Row) -> tuple:
    return (not row.expired, row.priority, row.kind == "table", row.effective or "", row.fetched_at or "")


def _kind_label(kind: str) -> str:
    return {"apy": "APY", "apr": "APR", "rate": "rate", "dividend": "dividend rate", "percent": ""}.get(kind, "")


def _format_row(row: Row, q: str) -> str:
    deposit = row.family in {"certificate", "share", "savings", "mma", "hysa", "hsa", "checking", "ira"}
    asks_apy = bool(re.search(r"\bapys?\b", q))
    asks_apr = bool(re.search(r"\baprs?\b", q))
    asks_div = "dividend" in q
    asks_rate = bool(re.search(r"\binterest rate|\brates?\b", q))
    want_intro = bool(re.search(r"\bintro|\bpromo", q))
    want_standard = bool(re.search(r"\bstandard\b|\bongoing\b|\bregular\b|\bnon-?intro|\bafter the", q))
    has_intro_cols = any(_is_intro_header(h) for _k, _p, _d, h in row.measures)
    shown = []
    kinds = [k for k, _p, _d, _h in row.measures]
    for kind, parsed, disp, header in row.measures:
        if kind not in RATE_KINDS:
            continue
        if deposit:
            if asks_apy and not asks_div and kind != "apy" and "apy" in kinds:
                continue
            if asks_div and not asks_apy and kind == "apy" and "dividend" in kinds:
                pass
        else:
            if asks_apr and not asks_rate and kind != "apr" and "apr" in kinds:
                continue
        label = _kind_label(kind)
        is_intro = _is_intro_header(header)
        if (want_standard and is_intro) or (want_intro and has_intro_cols and not is_intro):
            continue
        intro = " (introductory)" if is_intro else ""
        shown.append(f"{disp} {label}{intro}".strip())
    if not shown:
        return ""
    name = row.display_name()
    if row.kind == "line" and len(shown) == 1:
        measure_word = shown[0].split(" ", 1)[1] if " " in shown[0] else ""
        if measure_word and re.search(rf"\b{re.escape(measure_word)}\b", name, re.I):
            shown[0] = shown[0].split(" ", 1)[0]
    return f"{name}: {', '.join(shown)}."


def _extreme(chosen, highest: bool):
    best = None
    best_v = None
    for primary, agree in chosen:
        for kind, parsed, _d, _h in primary.measures:
            if kind not in RATE_KINDS or parsed[0] != "point":
                continue
            v = parsed[1]
            if best is None or (v > best_v if highest else v < best_v):
                best, best_v = (primary, agree), v
    return [best] if best else chosen[:1]


def _trim_sentence(sent: str, limit: int) -> str:
    s = re.sub(r"\s+", " ", sent).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def _compose_block(blk: dict) -> str:
    text = blk["text"]
    heading = (blk.get("heading") or "").strip()
    if len(text) > 700:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        out = ""
        for s in sentences:
            if len(out) + len(s) > 700 and out:
                break
            out += (" " if out else "") + s
        text = out
    if heading and len(heading) <= 110 and heading.lower() not in text.lower():
        return f"{heading}: {text}"
    return text


def _normalize_href(href: str) -> str:
    try:
        p = urlparse(href)
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    frag = p.fragment or ""
    if frag and (len(frag) > 24 or re.search(r"[_\d]", frag)):
        frag = ""
    path = p.path or "/"
    out = f"{p.scheme}://{p.netloc}{path}"
    if frag:
        out += f"#{frag}"
    return out


def _url_path_key(u: str) -> str:
    p = urlparse(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/").lower())


def _named_branch(q: str) -> str:
    m = re.search(r"(?:for|at|of)\s+(?:the|our|your)\s+([a-z0-9][a-z0-9 .'\-]{1,40}?)\s+branch", q)
    if not m:
        m = re.search(r"\b([a-z0-9][a-z0-9 .'\-]{1,40}?)\s+branch(?:'s)?\s+(?:hours|lobby|drive)", q)
    if not m:
        return ""
    name = m.group(1).strip()
    if name in {"a", "the", "our", "your", "every", "each", "all", "any", "general", "main", "local", "nearest", "closest"}:
        return ""
    return name


def _is_hours_question(q: str) -> bool:
    if re.search(r"\bhours\b", q):
        return True
    return bool(re.search(r"\b(open|close|closing|opening)\b", q) and re.search(r"\bwhat time\b|\btimes?\b", q))


def _is_link_question(q: str) -> bool:
    if re.search(r"\b(link|url|web address|website|webpage|web page)\b", q):
        return True
    return bool(re.search(r"\bonline\b", q) and re.search(r"\b(join|apply|sign up|open|enroll|become)\b", q))


def _refuse_line(q: str) -> str:
    fam = _wants_product(q)
    if fam == "certificate":
        return "I don't know. That certificate rate isn't listed on the website. Please contact us."
    if fam in {"mortgage", "equity"}:
        return "I don't know. That home-loan rate isn't listed on the website. Please contact a mortgage lender."
    return "I don't know. That rate isn't listed on the website. Please contact us."


def _range_compatible(pa, pb) -> bool:
    """A point that sits inside the other value's published range is not a contradiction."""
    if pa[0] == "point" and pb[0] == "range":
        return pb[1] - 1e-6 <= pa[1] <= pb[2] + 1e-6
    if pb[0] == "point" and pa[0] == "range":
        return pa[1] - 1e-6 <= pb[1] <= pa[2] + 1e-6
    return False


def _tiers_compatible(a: Row, b: Row) -> bool:
    if a.year_span or b.year_span:
        if not (a.year_span and b.year_span):
            return False
        return _overlaps(a.year_span, b.year_span)
    if a.balance or b.balance:
        if not (a.balance and b.balance):
            return False
        return _overlaps(a.balance, b.balance)
    return True


def _conflict_key(row: Row):
    """Coarse identity of a comparable fact: family, term and flags.  Product names (subtype)
    and tiers are compared pairwise with _subtype_compatible / _tiers_compatible."""
    if row.family == "certificate":
        return ("certificate", tuple(sorted(row.term_months)), tuple(sorted(row.term_years)), row.is_jumbo,
                row.is_special, row.is_ira, row.is_business)
    label = _norm(row.label) if (not _is_qualifier_text(row.label) and row.kind == "table") else ""
    return (row.family, label, tuple(sorted(row.term_months)), tuple(sorted(row.term_years)),
            row.is_ira, row.is_business, row.is_special, row.is_jumbo)


def _measures_conflict(va: dict, vb: dict) -> bool:
    common = set(va) & set(vb)
    if not common:
        return False
    for k in common:
        if _value_token(va[k]) != _value_token(vb[k]) and not _range_compatible(va[k], vb[k]):
            return True
    return False


def _measure_string(vals: dict, kinds: list) -> str:
    parts = [f"{_value_token(vals[k])} {_kind_label(k)}".strip() for k in kinds if k in vals]
    return " / ".join(parts)


def _topic_name(row: Row) -> str:
    name = row.display_name()
    name = re.sub(r"\b(\d+)\s+(months?)\b", lambda m: f"{m.group(1)}-{m.group(2).lower()}", name, flags=re.I)
    name = re.sub(r"\b(\d+)\s+(years?)\b", lambda m: f"{m.group(1)}-{m.group(2).lower()}", name, flags=re.I)
    return name


def _reason_for(winner: Row, loser: Optional[Row], snap: SnapIndex) -> str:
    w = f"{_describe_page(winner.url, winner.priority)}"
    if winner.effective:
        w += f" (rates effective {winner.effective})"
    elif winner.pub:
        w += f" (published {winner.pub})"
    text = f"{winner.url} is {w} and gives the resolved value."
    if loser is not None:
        l = _describe_page(loser.url, loser.priority)
        if loser.expired:
            l += " whose offer is marked as ended"
        elif loser.effective:
            l += f" (rates effective {loser.effective})"
        elif loser.pub:
            l += f" (published {loser.pub})"
        elif loser.fetched_at and winner.fetched_at and loser.fetched_at < winner.fetched_at:
            l += f" (last fetched {loser.fetched_at}, before {winner.fetched_at})"
        text += f" {loser.url} is {l} and still shows a different value in the same snapshot ({snap.snapshot_id})."
    return text


def build():
    return Knowledge()
