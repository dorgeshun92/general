"""scripts/common/masking.py: SSNs and account numbers never appear unmasked in reports or logs."""
from __future__ import annotations

import re

import pytest

from scripts.common.masking import contains_unmasked_pii, mask_account, mask_ssn, mask_text

# The schema's masked_account pattern (schemas/common.defs.schema.json).
MASKED_ACCOUNT_RE = re.compile(r"^\*{2,}[0-9A-Za-z]{2,4}$")


class TestMaskAccount:
    def test_none_passes_through(self):
        assert mask_account(None) is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("123456781234", "********1234"),
            ("987654321", "*****4321"),
            ("1234 5678 9012", "********9012"),   # whitespace stripped before masking
            ("ACCT98765", "*****8765"),
            ("12345", "**2345"),                  # at least two asterisks
        ],
    )
    def test_keeps_only_last_four(self, raw, expected):
        out = mask_account(raw)
        assert out == expected
        assert MASKED_ACCOUNT_RE.match(out)

    def test_never_leaks_more_than_last_four_digits(self):
        raw = "555512345678"
        out = mask_account(raw)
        assert raw[:-4] not in out
        assert out.endswith(raw[-4:])

    def test_custom_keep(self):
        assert mask_account("123456789", keep=2) == "*******89"

    def test_short_values_are_fully_masked(self):
        assert mask_account("1234") == "****"
        assert mask_account("12") == "****"
        assert "1" not in mask_account("1234")

    def test_accepts_non_string_input(self):
        assert mask_account(123456781234) == "********1234"


class TestMaskSsn:
    def test_none_passes_through(self):
        assert mask_ssn(None) is None

    @pytest.mark.parametrize("raw", ["123-45-6789", "123 45 6789", "123456789", "SSN: 123-45-6789"])
    def test_ssn_shapes_keep_last_four(self, raw):
        assert mask_ssn(raw) == "***-**-6789"

    def test_non_ssn_falls_back_to_account_masking(self):
        assert mask_ssn("98765") == "**8765"

    def test_masked_output_has_no_leading_digits(self):
        out = mask_ssn("123-45-6789")
        assert "123" not in out and "45" not in out


class TestMaskText:
    def test_masks_dashed_ssn(self):
        assert mask_text("Borrower SSN 123-45-6789 on file") == "Borrower SSN ***-**-6789 on file"

    def test_masks_spaced_and_bare_ssn(self):
        assert mask_text("123 45 6789") == "***-**-6789"
        assert mask_text("ssn=123456789;") == "ssn=***-**-6789;"

    def test_masks_eight_plus_digit_runs(self):
        assert mask_text("acct 12345678") == "acct ****5678"
        assert mask_text("acct 123456781234 end") == "acct ********1234 end"

    def test_seven_digit_run_is_left_alone(self):
        assert mask_text("ref 1234567") == "ref 1234567"

    @pytest.mark.parametrize(
        "untouched",
        [
            "$1,234.56",
            "2026-09-08",
            "Balance 48,210.55 as of 2026-07-31",
            "$400,000.00",
            "$12345678.00",        # currency-prefixed amounts are not account numbers
            "1,234,567.89",
        ],
    )
    def test_money_and_dates_are_untouched(self, untouched):
        assert mask_text(untouched) == untouched

    def test_mixed_line(self):
        line = "SSN 123-45-6789 acct 123456781234 paid $1,234.56 on 2026-09-08"
        assert mask_text(line) == "SSN ***-**-6789 acct ********1234 paid $1,234.56 on 2026-09-08"

    def test_already_masked_text_is_stable(self):
        masked = "***-**-6789 ****1234 $1,234.56"
        assert mask_text(masked) == masked

    def test_idempotent(self):
        once = mask_text("id 123-45-6789 / 555512345678")
        assert mask_text(once) == once

    def test_multiple_occurrences(self):
        out = mask_text("111-22-3333 and 444-55-6666")
        assert out == "***-**-3333 and ***-**-6666"

    def test_empty_string(self):
        assert mask_text("") == ""


class TestContainsUnmaskedPii:
    def test_clean_text_is_empty_list(self):
        assert contains_unmasked_pii("Loan LN-EXAMPLE-0001 balance $48,210.55 on 2026-07-31") == []

    def test_detects_ssn(self):
        hits = contains_unmasked_pii("ssn 123-45-6789")
        assert hits == ["SSN-shaped value"]

    def test_detects_long_digit_run(self):
        hits = contains_unmasked_pii("account 123456781234")
        assert hits == ["8+ digit run (possible account number)"]

    def test_detects_both(self):
        hits = contains_unmasked_pii("123-45-6789 / 123456781234")
        assert set(hits) == {"SSN-shaped value", "8+ digit run (possible account number)"}

    def test_masked_values_are_clean(self):
        assert contains_unmasked_pii("***-**-6789 ****1234") == []

    def test_mask_text_output_is_always_clean(self):
        dirty = "SSN 123-45-6789 acct 123456781234 other 987654321 amount $9,999,999.99"
        assert contains_unmasked_pii(mask_text(dirty)) == []
