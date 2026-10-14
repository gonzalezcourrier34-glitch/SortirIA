"""Tests unitaires de `app/trace_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_trace_service.py` (commit 18f7ce1).
"""

import json
from datetime import date, datetime
from pathlib import Path

from app.trace_service import TraceService


# -------------------------------------------------------------------------
# _json_default
# -------------------------------------------------------------------------

def test_json_default_date():
    service = TraceService()

    result = service._json_default(date(2026, 3, 25))

    assert result == "2026-03-25"


def test_json_default_datetime():
    service = TraceService()

    value = datetime(2026, 3, 25, 10, 30, 45)
    result = service._json_default(value)

    assert result == "2026-03-25T10:30:45"


def test_json_default_path():
    service = TraceService()

    path = Path("artifacts/test.jsonl")
    result = service._json_default(path)

    assert result == str(path)


def test_json_default_fallback_to_string():
    service = TraceService()

    class Dummy:
        def __str__(self):
            return "dummy-value"

    result = service._json_default(Dummy())

    assert result == "dummy-value"


# -------------------------------------------------------------------------
# write_trace
# -------------------------------------------------------------------------

def test_write_trace_disabled_does_nothing(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    service = TraceService(trace_file=trace_file, enabled=False)

    service.write_trace({"question": "test", "answer": "ok"})

    assert not trace_file.exists()


def test_write_trace_creates_parent_and_writes_jsonl(tmp_path):
    trace_file = tmp_path / "artifacts" / "rag_trace.jsonl"
    service = TraceService(trace_file=trace_file, enabled=True)

    service.write_trace({"question": "test", "answer": "ok"})

    assert trace_file.exists()

    content = trace_file.read_text(encoding="utf-8").strip()
    row = json.loads(content)

    assert "timestamp_utc" in row
    assert row["question"] == "test"
    assert row["answer"] == "ok"


def test_write_trace_appends_multiple_lines(tmp_path):
    trace_file = tmp_path / "rag_trace.jsonl"
    service = TraceService(trace_file=trace_file, enabled=True)

    service.write_trace({"question": "q1"})
    service.write_trace({"question": "q2"})

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 2

    row_1 = json.loads(lines[0])
    row_2 = json.loads(lines[1])

    assert row_1["question"] == "q1"
    assert row_2["question"] == "q2"


def test_write_trace_serializes_date_datetime_and_path(tmp_path):
    trace_file = tmp_path / "rag_trace.jsonl"
    service = TraceService(trace_file=trace_file, enabled=True)

    path_value = Path("artifacts/test.jsonl")

    service.write_trace(
        {
            "run_date": date(2026, 3, 25),
            "run_datetime": datetime(2026, 3, 25, 10, 30, 45),
            "file_path": path_value,
        }
    )

    content = trace_file.read_text(encoding="utf-8").strip()
    row = json.loads(content)

    assert row["run_date"] == "2026-03-25"
    assert row["run_datetime"] == "2026-03-25T10:30:45"
    assert row["file_path"] == str(path_value)


def test_write_trace_keeps_non_ascii_characters(tmp_path):
    trace_file = tmp_path / "rag_trace.jsonl"
    service = TraceService(trace_file=trace_file, enabled=True)

    service.write_trace({"question": "événement à Sète"})

    content = trace_file.read_text(encoding="utf-8").strip()
    row = json.loads(content)

    assert row["question"] == "événement à Sète"