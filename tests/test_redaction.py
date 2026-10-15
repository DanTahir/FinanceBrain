"""Unit tests for the redaction layer (analyzer, normalization, token format). No Redis or network needed."""

import pytest

from backend.redaction.recognizers import PiiAnalyzer
from backend.redaction.vault import normalize, token_type


def test_normalize_surface_formats_collapse():
    assert normalize("PHONE", "(201) 234-5678") == normalize("PHONE", "+1 201 234 5678") == "2012345678"
    assert normalize("PHONE", "201.234.5678") == "2012345678"
    assert normalize("DOB", "March 5, 1971") == normalize("DOB", "03/05/1971") == "1971-03-05"
    assert normalize("IBAN", "GB82 WEST 1234 5698 7654 32") == "GB82WEST12345698765432"
    assert normalize("CARD_NUMBER", "4111-1111-1111-1111") == "4111111111111111"
    assert normalize("CLIENT_NAME", "  Miguel   MACK ") == "miguel mack"


def test_token_type_roundtrip():
    assert token_type("⟨NAME_0042⟩") == "CLIENT_NAME"
    assert token_type("⟨ACCT_0001⟩") == "ACCOUNT_NUMBER"
    assert token_type("⟨PHONE_0007⟩") == "PHONE"
    assert token_type("PHONE_0007") is None


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer("halcyonridge.com", ["Miguel Mack", "Summit Ridge Logistics LLC"],
                       ["Rita Jimenez", "Paul Jensen"], ["PricingSvc", "Project Falcon"])


def test_detects_finance_and_contact_pii(analyzer):
    text = ("Wire from Miguel Mack, acct 5862944584, card 4111 1111 1111 1111, SSN 529-31-8842, "
            "call (201) 234-5678 or email miguel.mack7@gmail.com; DOB 1971-03-05; device DEV-0A1B2C3D4E5F; "
            "beneficiary IBAN GB82 WEST 1234 5698 7654 32.")
    found = {(d.type, text[d.start:d.end]) for d in analyzer.detect(text)}
    expected = {("CLIENT_NAME", "Miguel Mack"), ("ACCOUNT_NUMBER", "5862944584"),
                ("CARD_NUMBER", "4111 1111 1111 1111"), ("SSN", "529-31-8842"), ("PHONE", "(201) 234-5678"),
                ("EMAIL", "miguel.mack7@gmail.com"), ("DOB", "1971-03-05"), ("DEVICE_ID", "DEV-0A1B2C3D4E5F"),
                ("IBAN", "GB82 WEST 1234 5698 7654 32")}
    assert expected <= found, expected - found


def test_keeps_directory_data_and_business_terms(analyzer):
    text = ("Looping in rita.jimenez@halcyonridge.com. Rita Jimenez's team and Paul J. will review "
            "PricingSvc latency. Project Falcon stays restricted. Wire of $48,000 settled on 2026-09-25.")
    assert analyzer.detect(text) == []


def test_salary_needs_compensation_context(analyzer):
    plain = "The wire for $135,000, effective today, cleared."
    assert analyzer.detect(plain) == []
    hr = "The updated figure is $135,000, effective with the next payroll cycle."
    dets = analyzer.detect(hr, hint="Confidential: compensation adjustment")
    assert [(d.type, hr[d.start:d.end]) for d in dets] == [("SALARY", "$135,000")]
