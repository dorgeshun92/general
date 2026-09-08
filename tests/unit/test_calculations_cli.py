"""Tests for `python -m scripts.calculations <method> --json ...`."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.calculations.__main__ import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "scripts.calculations", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


def test_success_prints_json_trail():
    proc = run_cli("salaried_monthly_base", "--json", '{"period_gross": "2307.69", "pay_frequency": "BIWEEKLY"}')
    assert proc.returncode == 0, proc.stderr
    trail = json.loads(proc.stdout)
    assert trail["method"] == "salaried_monthly_base"
    assert trail["output"]["monthly_base"] == "5000.00"
    assert trail["rounding_policy"]


def test_unsupported_exits_3():
    proc = run_cli("unsupported", "--json", '{"kind": "COMMISSION"}')
    assert proc.returncode == 3
    assert "UNSUPPORTED" in proc.stderr
    assert "licensed reviewer" in proc.stderr
    assert proc.stdout == ""


def test_validation_error_exits_1():
    proc = run_cli("hourly_monthly_base", "--json",
                   '{"hourly_rate": "25", "verified_hours_per_week": "200", "verification_source": "VOE"}')
    assert proc.returncode == 1
    assert "ERROR" in proc.stderr and "168" in proc.stderr


def test_json_float_rejected_exits_1():
    proc = run_cli("salaried_monthly_base", "--json", '{"annual_salary": 60000.0}')
    assert proc.returncode == 1
    assert "decimal strings" in proc.stderr


def test_json_int_as_money_rejected_exits_1():
    proc = run_cli("salaried_monthly_base", "--json", '{"annual_salary": 60000}')
    assert proc.returncode == 1


def test_int_day_count_accepted_from_json():
    proc = run_cli("date_expiration_check", "--json",
                   '{"document_date": "2025-01-01", "as_of_date": "2025-05-01", "max_age_days": 120}')
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["output"]["days_remaining"] == 0


@pytest.mark.parametrize(
    "method, payload",
    [
        ("no_such_method", "{}"),
        ("salaried_monthly_base", "{not json"),
        ("salaried_monthly_base", '["60000"]'),
        ("salaried_monthly_base", '{"annual_salary": "60000", "bogus": "1"}'),
        ("salaried_monthly_base", '{}'),
    ],
)
def test_usage_problems_exit_1(method, payload):
    assert main([method, "--json", payload]) == 1


def test_main_in_process_exit_codes(capsys):
    assert main(["unsupported", "--json", '{"kind": "RENTAL"}']) == 3
    assert main(["statement_balance_reconciliation", "--json",
                 '{"beginning_balance": "1", "total_deposits": "1", "total_withdrawals": "0", "ending_balance": "2"}']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["output"]["reconciles"] is True


def test_missing_json_flag_is_usage_error():
    proc = run_cli("salaried_monthly_base")
    assert proc.returncode == 2
