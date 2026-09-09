import pytest

from moveai_contracts.dose import Dose, DoseField
from moveai_contracts.enums import SQL_ENUM_MAP


def test_sql_enum_parity(conn):
    for sql_name, py_enum in SQL_ENUM_MAP.items():
        labels = [r["l"] for r in conn.execute(
            "select e.enumlabel as l from pg_enum e join pg_type t on t.oid=e.enumtypid where t.typname=%s order by e.enumsortorder",
            (sql_name,)).fetchall()]
        assert labels == [m.value for m in py_enum], sql_name


def test_dose_null_requires_reason():
    with pytest.raises(ValueError):
        DoseField()
    f = DoseField(null_reason="not stated in source")
    assert not f.is_set


def test_dose_numbers_need_provenance_reference():
    with pytest.raises(ValueError):
        DoseField(value=3, provenance="source_explicit")
    with pytest.raises(ValueError):
        DoseField(value=3, provenance="clinician_authored")
    ok = DoseField(value=3, provenance="clinician_authored", author_id="u1")
    d = Dose(kind="prescribed", fields={"sets": ok, "repetitions": DoseField(value=10, provenance="unknown")})
    assert d.unsupported_numbers() == ["repetitions"]
    assert "hold_seconds" in d.missing_critical()


def test_unknown_dose_field_rejected():
    with pytest.raises(ValueError):
        Dose(fields={"vibes": DoseField(value=1, provenance="not_applicable")})
