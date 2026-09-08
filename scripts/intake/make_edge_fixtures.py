#!/usr/bin/env python3
"""Generate the synthetic edge-case fixtures under tests/fixtures/deidentified/LN-EDGE-*/.

    python scripts/intake/make_edge_fixtures.py [--root tests/fixtures/deidentified]

PDFs are written by hand (minimal PDF 1.4 syntax with Helvetica text) so no rendering library
is needed and every byte is reproducible. The only exception is the encrypted fixture, which
goes through pypdf's PdfWriter.encrypt(); its random salts are pinned to a deterministic
generator so re-running this script does not churn the committed file.

Everything here is synthetic: names are "Test Borrower ...", SSNs are pre-masked, and the
only 8+ digit numbers are obviously fake values that the matching MANIFEST.yaml allowlists
(so the live-PII heuristic can be shown to fire and be allowlisted).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "deidentified"
DEIDENTIFIED_BY = "fixture generator"
DEIDENTIFIED_AT = "2026-09-08"
ENCRYPTED_FIXTURE_PASSWORD = "fixture-password-never-used-by-tooling"


# --------------------------------------------------------------------------------------
# Minimal PDF writer
# --------------------------------------------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list[str] | None], producer: str = "mpire-fixture-generator") -> bytes:
    """Build a PDF. Each page is a list of text lines, or None for a page with no content
    stream at all (simulates a scan with no text layer)."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_obj = add(b"PLACEHOLDER")
    page_ids = []
    for lines in pages:
        content = None
        if lines is not None:
            ops = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
            ops += [f"({_esc(ln)}) Tj T*" for ln in lines]
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1")
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = (f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font} 0 R >> >>")
        if content:
            page += f" /Contents {content} 0 R"
        page_ids.append(add((page + " >>").encode()))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    catalog = add(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode())
    info = add(f"<< /Producer ({_esc(producer)}) >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
              f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def encrypt_pdf(data: bytes, password: str) -> bytes:
    """Encrypt with pypdf, pinning its random salt source so output bytes are reproducible."""
    import importlib
    from unittest import mock

    from pypdf import PdfReader, PdfWriter

    class _FixedSecrets:
        """Stand-in for the `secrets` module: salts, keys, and AES IVs come from a counter."""
        counter = 0

        @classmethod
        def token_bytes(cls, n: int) -> bytes:
            cls.counter += 1
            return hashlib.sha256(f"mpire-fixture-{cls.counter}".encode()).digest()[:n]

    # pypdf draws randomness in two places: key/salt generation in pypdf._encryption and the
    # per-object AES IV inside the active crypt provider. Patch both.
    targets = ["pypdf._encryption"]
    for provider in ("pypdf._crypt_providers._cryptography", "pypdf._crypt_providers._pycryptodome"):
        try:
            importlib.import_module(provider)
            targets.append(provider)
        except ImportError:
            pass
    patches = [mock.patch(f"{t}.secrets", _FixedSecrets) for t in targets]
    for patch in patches:
        patch.start()
    try:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        writer.encrypt(password, algorithm="AES-128")
        buf = io.BytesIO()
        writer.write(buf)
    finally:
        for patch in patches:
            patch.stop()
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Synthetic document templates
# --------------------------------------------------------------------------------------
BANNER = "SYNTHETIC TEST DOCUMENT - NOT A REAL BORROWER, EMPLOYER, OR ACCOUNT"


def urla_1003(name: str) -> list[list[str]]:
    return [
        ["Uniform Residential Loan Application (Form 1003)", BANNER,
         f"Borrower Name: {name}", "Loan Purpose: Purchase", "Occupancy: Primary Residence",
         "Application Date: 08/15/2026", "Page 1 of 2"],
        ["Section 2: Financial Information - Assets and Liabilities", "Section 4: Loan and Property Information",
         "Page 2 of 2"],
    ]


def paystub(name: str, period: str = "08/01/2026 - 08/15/2026", pay_date: str = "08/20/2026") -> list[list[str]]:
    return [[
        "EARNINGS STATEMENT", BANNER, "Employer: Synthetic Widgets LLC",
        f"Employee: {name}", f"Pay Period: {period}", f"Pay Date: {pay_date}",
        "Gross Pay: $3,250.00", "Net Pay: $2,410.55", "YTD Gross: $52,000.00", "Page 1 of 1",
    ]]


def w2(name: str) -> list[list[str]]:
    return [[
        "Form W-2 Wage and Tax Statement 2025", BANNER, f"Employee's name: {name}",
        "Employee SSN: ***-**-0000", "Employer: Synthetic Widgets LLC",
        "Wages, tips, other compensation: $48,500.00", "Federal income tax withheld: $5,120.00",
    ]]


def bank_statement(name: str, account: str, reference: str, total_pages: int = 2,
                   period: str = "07/01/2026 - 07/31/2026") -> list[list[str]]:
    pages = [[
        "SYNTHETIC BANK OF TESTING", "Account Statement", BANNER,
        f"Account holder: {name}", f"Account number: {account}", f"Confirmation number: {reference}",
        f"Statement period: {period}",
        "Beginning balance: $10,000.00", "Ending balance: $12,500.00", f"Page 1 of {total_pages}",
    ]]
    pages.append(["Transaction detail", f"07/15/2026 Deposit Reference {reference} $2,500.00",
                  f"Page 2 of {total_pages}"])
    return pages


def purchase_contract(name: str) -> list[list[str]]:
    return [[
        "RESIDENTIAL PURCHASE AGREEMENT", BANNER, f"Buyer: {name}", "Seller: Test Seller Synthetic",
        "Property: 123 Example Street, Testville, CA 90000", "Purchase Price: $450,000.00",
        "Earnest Money Deposit: $5,000.00", "Contract Date: 08/10/2026", "Page 1 of 1",
    ]]


# --------------------------------------------------------------------------------------
# Loan directories
# --------------------------------------------------------------------------------------

def manifest_yaml(loan_id: str, description: str, allowlist: list[str] | None = None) -> str:
    lines = [
        f"loan_id: {loan_id}",
        "deidentified: true",
        f'deidentified_by: "{DEIDENTIFIED_BY}"',
        f'deidentified_at: "{DEIDENTIFIED_AT}"',
        f'description: "{description}"',
    ]
    if allowlist:
        lines.append("# Literal tokens the live-PII heuristic may ignore. Every value is a fake number")
        lines.append("# placed by the fixture generator; a human vouches for it by keeping it here.")
        lines.append("pii_pattern_allowlist:")
        lines += [f'  - "{a}"' for a in allowlist]
    return "\n".join(lines) + "\n"


def write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def make_all(root: Path) -> list[Path]:
    made: list[Path] = []

    def loan(loan_id: str, description: str, readme: str, files: dict[str, bytes | str],
             allowlist: list[str] | None = None) -> None:
        d = root / loan_id
        write(d / "MANIFEST.yaml", manifest_yaml(loan_id, description, allowlist))
        write(d / "README.md", f"# {loan_id}\n\n{readme.strip()}\n\nGenerated by `scripts/intake/make_edge_fixtures.py`. "
                               "All content is synthetic.\n")
        for rel, data in files.items():
            write(d / rel, data)
        made.append(d)

    # ---- LN-EDGE-CLEAN --------------------------------------------------------------
    name = "Test Borrower Clean"
    loan(
        "LN-EDGE-CLEAN",
        "Small clean synthetic package: 1003, paystub, W2, bank statement, purchase contract",
        """Tests the happy path: five clearly synthetic documents that classify at HIGH
confidence, one consistent borrower name, one subdirectory level, and a bank statement
carrying two fake 8-digit numbers (12345678, 87654321) that make the live-PII heuristic fire
and that MANIFEST.yaml explicitly allowlists.""",
        {
            "01_urla_1003.pdf": build_pdf(urla_1003(name)),
            "income/paystub_2026-08.pdf": build_pdf(paystub(name)),
            "income/w2_2025.pdf": build_pdf(w2(name)),
            "assets/bank_statement_2026-07.pdf": build_pdf(bank_statement(name, "12345678", "87654321")),
            "contract/purchase_contract.pdf": build_pdf(purchase_contract(name)),
        },
        allowlist=["12345678", "87654321"],
    )

    # ---- LN-EDGE-DUPLICATE ----------------------------------------------------------
    name = "Test Borrower Duplicate"
    stub = build_pdf(paystub(name))
    loan(
        "LN-EDGE-DUPLICATE",
        "Same paystub three times: identical bytes twice, plus a same-text-different-bytes copy",
        """Tests duplicate detection. `paystub_a.pdf` and `paystub_b.pdf` are byte-identical
(IDENTICAL_HASH). `paystub_c_resaved.pdf` has the same page text but a different Producer
string, so its hash differs (SAME_CONTENT_DIFFERENT_FILE).""",
        {
            "paystub_a.pdf": stub,
            "paystub_b.pdf": stub,
            "paystub_c_resaved.pdf": build_pdf(paystub(name), producer="another-synthetic-printer"),
        },
    )

    # ---- LN-EDGE-UNREADABLE ---------------------------------------------------------
    loan(
        "LN-EDGE-UNREADABLE",
        "Garbage bytes with a .pdf extension, an empty .pdf, a scan with no text layer, a .txt",
        """Tests unreadable handling: `garbage.pdf` is not a PDF (CORRUPT), `empty.pdf` is
zero bytes (EMPTY), `scan_no_text_layer.pdf` is a valid 2-page PDF with no text layer
(NO_TEXT_LAYER; OCR is recorded as not attempted), and `notes.txt` is listed as
UNSUPPORTED_FORMAT without being opened as a PDF.""",
        {
            "garbage.pdf": b"this is not a pdf at all, just synthetic filler bytes\n" * 40,
            "empty.pdf": b"",
            "scan_no_text_layer.pdf": build_pdf([None, None]),
            "notes.txt": "Synthetic processor note. Not a PDF. Nothing to extract here.\n",
        },
    )

    # ---- LN-EDGE-ENCRYPTED ----------------------------------------------------------
    name = "Test Borrower Locked"
    loan(
        "LN-EDGE-ENCRYPTED",
        "A password-protected PDF the tooling must report as ENCRYPTED without trying passwords",
        f"""Tests encrypted handling: `protected_bank_statement.pdf` is AES-128 encrypted with
the password `{ENCRYPTED_FIXTURE_PASSWORD}`. The password is published here only to prove the
tooling never uses it: intake must report ENCRYPTED and a review item, never decrypt.
`paystub.pdf` is a readable companion so the run still produces a normal inventory.""",
        {
            "protected_bank_statement.pdf": encrypt_pdf(
                build_pdf(bank_statement(name, "****4321", "****8765")), ENCRYPTED_FIXTURE_PASSWORD),
            "paystub.pdf": build_pdf(paystub(name)),
        },
    )

    # ---- LN-EDGE-MISSING-PAGES ------------------------------------------------------
    name = "Test Borrower Partial"
    loan(
        "LN-EDGE-MISSING-PAGES",
        "A bank statement whose footer says 'Page 1 of 3' but only two pages exist",
        """Tests missing-page detection: `bank_statement_partial.pdf` carries footers
"Page 1 of 3" and "Page 2 of 3" but the file has two pages, so intake flags
POSSIBLE_MISSING_PAGES.""",
        {
            "bank_statement_partial.pdf": build_pdf(bank_statement(name, "****2468", "****1357", total_pages=3,
                                                                   period="06/01/2026 - 06/30/2026")),
        },
    )

    # ---- LN-EDGE-CONFLICTING-NAMES --------------------------------------------------
    loan(
        "LN-EDGE-CONFLICTING-NAMES",
        "Three documents naming the borrower three different ways",
        """Tests identity conflict detection: the W2 says "Test Borrower Alpha", the paystub
says "Test Borrower Alpha-Smith", and the bank statement says "T. B. Alpha". Intake must
raise a CONFLICTING_IDENTITY review item rather than pick one.""",
        {
            "w2_2025.pdf": build_pdf(w2("Test Borrower Alpha")),
            "paystub_2026-08.pdf": build_pdf(paystub("Test Borrower Alpha-Smith")),
            "bank_statement_2026-07.pdf": build_pdf(bank_statement("T. B. Alpha", "****9900", "****1122")),
        },
    )
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args(argv)
    for d in make_all(args.root):
        print(f"wrote {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
