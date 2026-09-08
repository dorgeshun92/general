"""Deterministic keyword classifier and low-confidence field heuristics.

Everything here is intentionally simple and transparent: a keyword table, a handful of
regexes, and a confidence label on every result. The model performs judgment later; these
outputs are hints, not facts. Inputs are MASKED text (extract_text masks before returning),
so account numbers arrive already in ****1234 form.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# --------------------------------------------------------------------------------------
# Keyword table. Order matters: on a tie in marker hits, the earlier entry wins.
#   filename: lower-cased substrings matched against the file name (-> MEDIUM alone)
#   text:     lower-cased distinctive markers matched against first-page text (-> HIGH)
# Keep markers specific; generic words ("statement", "date") would cross-match.
# --------------------------------------------------------------------------------------
KEYWORD_TABLE: dict[str, dict[str, list[str]]] = {
    "URLA_1003": {
        "filename": ["1003", "urla", "loan application", "loan_application", "loan-application"],
        "text": ["uniform residential loan application", "form 1003", "urla"],
    },
    "CREDIT_REPORT": {
        "filename": ["credit report", "credit_report", "credit-report", "tri-merge", "trimerge"],
        "text": ["credit report", "tradeline", "equifax", "experian", "transunion"],
    },
    "PAYSTUB": {
        "filename": ["paystub", "pay stub", "pay_stub", "pay-stub", "earnings statement", "earnings_statement"],
        "text": ["earnings statement", "pay stub", "paystub", "pay period", "gross pay", "net pay"],
    },
    "W2": {
        "filename": ["w2", "w-2", "w_2"],
        "text": ["wage and tax statement", "form w-2", "w-2"],
    },
    "1099": {
        "filename": ["1099"],
        "text": ["form 1099", "1099-misc", "1099-nec", "1099-int", "1099-div"],
    },
    "TAX_RETURN_PERSONAL": {
        "filename": ["1040", "tax return", "tax_return", "tax-return"],
        "text": ["form 1040", "u.s. individual income tax return"],
    },
    "TAX_RETURN_BUSINESS": {
        "filename": ["1120", "1065"],
        "text": ["form 1120", "form 1065", "u.s. corporation income tax return", "u.s. return of partnership income"],
    },
    "K1": {"filename": ["k1", "k-1"], "text": ["schedule k-1"]},
    "PROFIT_AND_LOSS": {
        "filename": ["p&l", "pnl", "profit and loss", "profit_and_loss", "profit-and-loss"],
        "text": ["profit and loss", "profit & loss", "income statement"],
    },
    "VOE_WRITTEN": {
        "filename": ["voe", "verification of employment"],
        "text": ["verification of employment"],
    },
    "OFFER_LETTER": {"filename": ["offer letter", "offer_letter"], "text": ["offer of employment", "pleased to offer you"]},
    "BANK_STATEMENT": {
        "filename": ["bank statement", "bank_statement", "bank-statement", "checking", "savings", "stmt"],
        "text": ["statement period", "beginning balance", "ending balance", "account summary", "checking account", "savings account"],
    },
    "RETIREMENT_STATEMENT": {
        "filename": ["401k", "401(k)", "ira", "retirement"],
        "text": ["401(k)", "401k", "individual retirement", "vested balance"],
    },
    "BROKERAGE_STATEMENT": {"filename": ["brokerage"], "text": ["brokerage account", "portfolio summary"]},
    "GIFT_LETTER": {"filename": ["gift letter", "gift_letter"], "text": ["gift letter", "no repayment is expected"]},
    "PURCHASE_CONTRACT": {
        "filename": ["purchase contract", "purchase_contract", "purchase-contract", "purchase agreement", "purchase_agreement", "rpa", "contract"],
        "text": ["residential purchase agreement", "purchase agreement", "purchase contract", "purchase price", "earnest money deposit"],
    },
    "CONTRACT_ADDENDUM": {"filename": ["addendum"], "text": ["addendum to purchase", "addendum no"]},
    "APPRAISAL": {"filename": ["appraisal"], "text": ["uniform residential appraisal report", "appraised value"]},
    "TITLE_COMMITMENT": {"filename": ["title commitment", "title_commitment"], "text": ["commitment for title insurance"]},
    "PRELIM_TITLE": {"filename": ["prelim"], "text": ["preliminary report", "preliminary title"]},
    "HOMEOWNERS_INSURANCE": {
        "filename": ["hoi", "homeowners", "insurance binder", "evidence of insurance"],
        "text": ["evidence of insurance", "homeowners insurance", "dwelling coverage"],
    },
    "MORTGAGE_STATEMENT": {"filename": ["mortgage statement", "mortgage_statement"], "text": ["mortgage statement", "escrow balance", "principal balance"]},
    "LEASE_AGREEMENT": {"filename": ["lease"], "text": ["lease agreement", "tenant", "landlord"]},
    "PROPERTY_TAX_BILL": {"filename": ["tax bill", "tax_bill", "property tax"], "text": ["property tax bill", "secured property tax"]},
    "LETTER_OF_EXPLANATION": {"filename": ["loe", "letter of explanation", "explanation"], "text": ["letter of explanation", "to whom it may concern"]},
    "PHOTO_ID": {"filename": ["id", "license", "passport"], "text": ["driver license", "driver's license", "passport"]},
    "AUS_FINDINGS": {"filename": ["aus", "du findings", "lpa"], "text": ["desktop underwriter", "loan product advisor", "underwriting findings"]},
    "PREAPPROVAL_LETTER": {"filename": ["preapproval", "pre-approval", "pre_approval"], "text": ["pre-approval letter", "preapproval letter", "pre-approved for"]},
}

CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW = "HIGH", "MEDIUM", "LOW"


@dataclass
class Classification:
    document_type: str
    confidence: str
    matched_text_markers: list[str] = field(default_factory=list)
    matched_filename_tokens: list[str] = field(default_factory=list)
    note: str = ""


def _filename_matches(filename_lower: str, tokens: list[str]) -> list[str]:
    # Filename tokens are matched as substrings, but tokens of <= 3 chars (e.g. "id", "ira",
    # "rpa") must be whole words so "identity.pdf" is not PHOTO_ID and "aspirations" not IRA.
    stem = re.sub(r"[_\-.]+", " ", filename_lower)
    hits = []
    for tok in tokens:
        t = re.sub(r"[_\-.]+", " ", tok)
        if len(t) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", stem):
                hits.append(tok)
        elif t in stem:
            hits.append(tok)
    return hits


def classify(filename: str, first_page_text: str) -> Classification:
    """Return (document_type, confidence) from the keyword table.

    HIGH   at least one distinctive text marker matched (most hits wins; ties -> table order);
    MEDIUM only the filename matched;
    LOW    nothing matched -> UNKNOWN.
    Limitation: purely lexical. A cover page, a mislabeled file, or a mixed bundle will be
    misclassified or UNKNOWN; the model and a human review the result.
    """
    text = (first_page_text or "").lower()
    fname = (filename or "").lower()
    best_type, best_hits = None, []
    for doc_type, spec in KEYWORD_TABLE.items():
        hits = [m for m in spec["text"] if m in text]
        if len(hits) > len(best_hits):
            best_type, best_hits = doc_type, hits
    filename_choice, filename_hits = None, []
    for doc_type, spec in KEYWORD_TABLE.items():
        hits = _filename_matches(fname, spec["filename"])
        if hits:
            filename_choice, filename_hits = doc_type, hits
            break
    if best_type:
        note = ""
        if filename_choice and filename_choice != best_type:
            note = f"filename suggests {filename_choice} but page text matches {best_type}"
        return Classification(best_type, CONFIDENCE_HIGH, best_hits, filename_hits if filename_choice == best_type else [], note)
    if filename_choice:
        return Classification(filename_choice, CONFIDENCE_MEDIUM, [], filename_hits,
                              "classified from filename only; page text had no distinctive marker")
    return Classification("UNKNOWN", CONFIDENCE_LOW, [], [], "no filename or text marker matched")


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in MONTHS.items()})
MONTHS["sept"] = 9

DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_US_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DATE_LONG_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
# Non-capturing union of the three shapes, safe to embed inside other regexes.
DATE_TOKEN = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
DATE_ANY_RE = re.compile(r"\b" + DATE_TOKEN + r"\b", re.IGNORECASE)
PLAUSIBLE_YEARS = (1990, 2100)


def normalize_date(token: str) -> str | None:
    """ISO-normalize one date token or return None. US m/d/yyyy is assumed (never d/m/yyyy)."""
    token = token.strip()
    try:
        m = DATE_ISO_RE.fullmatch(token)
        if m:
            y, mo, d = (int(g) for g in m.groups())
        else:
            m = DATE_US_RE.fullmatch(token)
            if m:
                mo, d, y = (int(g) for g in m.groups())
            else:
                m = DATE_LONG_RE.fullmatch(token)
                if not m:
                    return None
                mo = MONTHS.get(m.group(1).lower().rstrip("."))
                if mo is None:
                    return None
                d, y = int(m.group(2)), int(m.group(3))
        if not (PLAUSIBLE_YEARS[0] <= y <= PLAUSIBLE_YEARS[1]):
            return None
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


@dataclass
class DateCandidate:
    iso: str
    raw: str
    line: str
    confidence: str


def find_dates(text: str) -> list[DateCandidate]:
    """All plausible dates in document order. MEDIUM when the line mentions 'date', else LOW."""
    out = []
    for line in (text or "").splitlines():
        for m in DATE_ANY_RE.finditer(line):
            iso = normalize_date(m.group(0))
            if iso:
                conf = CONFIDENCE_MEDIUM if "date" in line.lower() else CONFIDENCE_LOW
                out.append(DateCandidate(iso, m.group(0), line.strip()[:120], conf))
    return out


def document_date(text: str) -> tuple[str | None, str]:
    """First plausible date in the text, preferring a date on a line that mentions 'date'.
    Limitation: statement periods, birth dates, and print dates all look alike to this."""
    cands = find_dates(text)
    if not cands:
        return None, CONFIDENCE_LOW
    for c in cands:
        if c.confidence == CONFIDENCE_MEDIUM:
            return c.iso, CONFIDENCE_MEDIUM
    return cands[0].iso, CONFIDENCE_LOW


# --------------------------------------------------------------------------------------
# Statement period
# --------------------------------------------------------------------------------------
_D = DATE_TOKEN
PERIOD_RES = [
    re.compile(rf"statement\s+period\s*[:\-]?\s*({_D})\s*(?:-|–|—|to|through|thru)\s*({_D})", re.IGNORECASE),
    re.compile(rf"(?:period|for the period)\s*[:\-]?\s*({_D})\s*(?:-|–|—|to|through|thru)\s*({_D})", re.IGNORECASE),
    re.compile(rf"from\s+({_D})\s+(?:to|through|thru)\s+({_D})", re.IGNORECASE),
]


def statement_period(text: str) -> tuple[dict, str]:
    """{'start','end'} ISO dates from 'Statement period', 'Period', or 'from X to Y' patterns.
    HIGH for an explicit 'statement period' label, MEDIUM for 'period'/'from..to', LOW = none."""
    flat = " ".join((text or "").split())
    for rank, rx in enumerate(PERIOD_RES):
        m = rx.search(flat)
        if m:
            start, end = normalize_date(m.group(1)), normalize_date(m.group(2))
            if start and end and start <= end:
                return {"start": start, "end": end}, (CONFIDENCE_HIGH if rank == 0 else CONFIDENCE_MEDIUM)
    return {"start": None, "end": None}, CONFIDENCE_LOW


# --------------------------------------------------------------------------------------
# Borrower name candidates
# --------------------------------------------------------------------------------------
NAME_MARKERS = ["borrower name", "borrower", "co-borrower", "employee name", "employee's name", "employee",
                "account holder", "accountholder", "name", "buyer", "prepared for"]
NAME_MARKER_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(m) for m in NAME_MARKERS) + r")\s*(?:\(s\))?\s*[:\-]\s*(.*)$", re.IGNORECASE)
NAME_OK_RE = re.compile(r"^[A-Za-z][A-Za-z.'\- ]{1,59}$")
NAME_STOPWORDS = {"n/a", "none", "same", "see above"}


def borrower_name_candidates(text: str) -> list[str]:
    """Names on lines that start with a marker such as 'Borrower:', 'Employee:', 'Account holder:',
    'Name:' or 'Buyer:' (value on the same line, else the next non-empty line). Always LOW
    confidence: company names, addresses, and labels like 'Borrower: see page 2' slip through,
    and a name split across lines is missed."""
    # pypdf renders an ASCII apostrophe in Helvetica as a curly quote (’), so
    # "Employee's name:" arrives as "Employee’s name:". Normalize before matching.
    normalized = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    lines = [ln.strip() for ln in normalized.splitlines()]
    found: list[str] = []
    for i, line in enumerate(lines):
        m = NAME_MARKER_RE.match(line)
        if not m:
            continue
        value = m.group(1).strip()
        if not value:
            value = next((ln for ln in lines[i + 1:i + 3] if ln), "")
        value = re.split(r"\s{2,}|\t|\s+(?:ssn|id|date|address)\b", value, flags=re.IGNORECASE)[0].strip(" ,;")
        if value and value.lower() not in NAME_STOPWORDS and NAME_OK_RE.match(value) and value not in found:
            found.append(value)
    return found


def normalize_name(name: str) -> str:
    """Lower-case, strip punctuation, collapse spaces - for comparing names across documents."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())


# --------------------------------------------------------------------------------------
# Masked account numbers
# --------------------------------------------------------------------------------------
# Input text is already masked, so every 8+ digit run appears as "****1234". SSNs appear as
# "***-**-1234" and are excluded by the leading-context check.
MASKED_ACCOUNT_RE = re.compile(r"(?<![\w*\-])(\*{2,}[0-9A-Za-z]{2,4})(?![\w*])")


def masked_account_numbers(masked_text: str) -> list[str]:
    """Distinct masked account tokens in document order. Limitation: account numbers with
    internal spaces or fewer than 8 digits are never masked by mask_text and so are not found."""
    out: list[str] = []
    for m in MASKED_ACCOUNT_RE.finditer(masked_text or ""):
        tok = m.group(1)
        if tok not in out:
            out.append(tok)
    return out


@dataclass
class ExtractedFields:
    document_date: str | None
    document_date_confidence: str
    date_candidates: list[dict]
    statement_period: dict
    statement_period_confidence: str
    borrower_name_candidates: list[str]
    borrower_name_confidence: str
    masked_account_numbers: list[str]


def extract_fields(masked_text: str) -> ExtractedFields:
    """Run every field heuristic over (masked) document text and label each with confidence."""
    dd, dd_conf = document_date(masked_text)
    period, period_conf = statement_period(masked_text)
    names = borrower_name_candidates(masked_text)
    return ExtractedFields(
        document_date=dd,
        document_date_confidence=dd_conf,
        date_candidates=[{"iso": c.iso, "raw": c.raw, "confidence": c.confidence} for c in find_dates(masked_text)],
        statement_period=period,
        statement_period_confidence=period_conf,
        borrower_name_candidates=names,
        borrower_name_confidence=CONFIDENCE_LOW,
        masked_account_numbers=masked_account_numbers(masked_text),
    )
