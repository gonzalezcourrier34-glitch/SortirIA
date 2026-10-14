"""Tests unitaires de `app/memory_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_memory_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

import json

from app.memory_service import MemoryService


def test_get_history_returns_empty_list_when_file_not_exists(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service.get_history("session_1") == []


def test_read_data_invalid_json_returns_empty_dict(tmp_path):
    memory_file = tmp_path / "memory.json"
    memory_file.write_text("{invalid json", encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service._read_data() == {}


def test_read_data_valid_json_but_not_dict_returns_empty_dict(tmp_path):
    memory_file = tmp_path / "memory.json"
    memory_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service._read_data() == {}


def test_reset_memory_creates_empty_dict_file(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.reset_memory()

    assert service.memory_file.exists()
    data = json.loads(service.memory_file.read_text(encoding="utf-8"))
    assert data == {}


def test_append_message_and_get_history(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
        max_turns=3,
    )

    service.append_message("session_1", "user", "Bonjour")
    service.append_message("session_1", "assistant", "Salut")

    history = service.get_history("session_1")

    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Bonjour"}
    assert history[1] == {"role": "assistant", "content": "Salut"}


def test_append_message_ignores_invalid_session_id(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("", "user", "Bonjour")
    service.append_message("   ", "assistant", "Salut")

    assert service.get_history("session_1") == []


def test_append_message_ignores_invalid_role(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "system", "Bonjour")
    service.append_message("session_1", "", "Salut")

    assert service.get_history("session_1") == []


def test_append_message_ignores_empty_content(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "")
    service.append_message("session_1", "assistant", "   ")

    assert service.get_history("session_1") == []


def test_append_message_normalizes_role_and_content(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", " USER ", "  Bonjour  ")

    history = service.get_history("session_1")

    assert history == [{"role": "user", "content": "Bonjour"}]


def test_histories_are_isolated_by_session(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_a", "user", "Question A")
    service.append_message("session_b", "user", "Question B")

    history_a = service.get_history("session_a")
    history_b = service.get_history("session_b")

    assert history_a == [{"role": "user", "content": "Question A"}]
    assert history_b == [{"role": "user", "content": "Question B"}]


def test_get_history_respects_max_turns(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
        max_turns=2,
    )

    service.append_message("session_1", "user", "Q1")
    service.append_message("session_1", "assistant", "R1")
    service.append_message("session_1", "user", "Q2")
    service.append_message("session_1", "assistant", "R2")
    service.append_message("session_1", "user", "Q3")
    service.append_message("session_1", "assistant", "R3")

    history = service.get_history("session_1")

    assert len(history) == 4
    assert history == [
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "R2"},
        {"role": "user", "content": "Q3"},
        {"role": "assistant", "content": "R3"},
    ]


def test_get_history_filters_invalid_items_from_file(tmp_path):
    memory_file = tmp_path / "memory.json"
    payload = {
        "session_1": [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Salut"},
            {"role": "system", "content": "Ignore-moi"},
            {"role": "user", "content": ""},
            "not a dict",
            {"role": "", "content": "xxx"},
        ]
    }
    memory_file.write_text(json.dumps(payload), encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    history = service.get_history("session_1")

    assert history == [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Salut"},
    ]


def test_get_history_returns_empty_when_session_content_is_not_a_list(tmp_path):
    memory_file = tmp_path / "memory.json"
    payload = {"session_1": {"role": "user", "content": "Bonjour"}}
    memory_file.write_text(json.dumps(payload), encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service.get_history("session_1") == []


def test_clear_session_removes_only_target_session(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Bonjour")
    service.append_message("session_2", "user", "Salut")

    service.clear_session("session_1")

    assert service.get_history("session_1") == []
    assert service.get_history("session_2") == [
        {"role": "user", "content": "Salut"}
    ]


def test_clear_session_with_empty_session_id_does_nothing(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Bonjour")
    service.clear_session("")

    assert service.get_history("session_1") == [
        {"role": "user", "content": "Bonjour"}
    ]


def test_format_history_for_prompt_returns_empty_string_when_no_history(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service.format_history_for_prompt("session_1") == ""


def test_format_history_for_prompt_formats_messages_correctly(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Je veux une expo")
    service.append_message("session_1", "assistant", "Voici des expositions")

    formatted = service.format_history_for_prompt("session_1")

    assert formatted == (
        "Utilisateur : Je veux une expo\n"
        "Assistant : Voici des expositions"
    )


def test_list_sessions_returns_sorted_session_ids(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_b", "user", "Bonjour")
    service.append_message("session_a", "user", "Salut")

    assert service.list_sessions() == ["session_a", "session_b"]


def test_has_session_returns_false_when_session_is_missing(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service.has_session("unknown_session") is False


def test_has_session_returns_true_when_session_contains_valid_messages(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Bonjour")

    assert service.has_session("session_1") is True


def test_has_session_returns_false_for_blank_session_id(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    assert service.has_session("") is False
    assert service.has_session("   ") is False


def test_get_recent_messages_returns_full_history_when_max_messages_is_none(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Q1")
    service.append_message("session_1", "assistant", "R1")

    recent = service.get_recent_messages("session_1")

    assert recent == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "R1"},
    ]


def test_get_recent_messages_applies_limit(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
        max_turns=3,
    )

    service.append_message("session_1", "user", "Q1")
    service.append_message("session_1", "assistant", "R1")
    service.append_message("session_1", "user", "Q2")
    service.append_message("session_1", "assistant", "R2")
    service.append_message("session_1", "user", "Q3")

    recent = service.get_recent_messages("session_1", max_messages=2)

    assert recent == [
        {"role": "assistant", "content": "R2"},
        {"role": "user", "content": "Q3"},
    ]


def test_get_recent_messages_with_invalid_max_messages_falls_back_to_default_window(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
        max_turns=2,
    )

    service.append_message("session_1", "user", "Q1")
    service.append_message("session_1", "assistant", "R1")
    service.append_message("session_1", "user", "Q2")
    service.append_message("session_1", "assistant", "R2")

    recent = service.get_recent_messages("session_1", max_messages="invalid")

    assert recent == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "R1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "R2"},
    ]


def test_get_recent_messages_with_zero_max_messages_returns_at_least_one(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Bonjour")
    service.append_message("session_1", "assistant", "Salut")

    recent = service.get_recent_messages("session_1", max_messages=0)

    assert recent == [{"role": "assistant", "content": "Salut"}]


def test_append_turn_adds_user_and_assistant_messages(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_turn(
        "session_1",
        user_message="Je veux une expo",
        assistant_message="Voici une expo",
    )

    history = service.get_history("session_1")

    assert history == [
        {"role": "user", "content": "Je veux une expo"},
        {"role": "assistant", "content": "Voici une expo"},
    ]


def test_prune_empty_sessions_removes_invalid_or_empty_entries(tmp_path):
    memory_file = tmp_path / "memory.json"
    payload = {
        "session_1": [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Salut"},
        ],
        "session_2": [],
        "session_3": [
            {"role": "system", "content": "invalid"},
            {"role": "user", "content": ""},
        ],
        "": [
            {"role": "user", "content": "hidden"},
        ],
    }
    memory_file.write_text(json.dumps(payload), encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.prune_empty_sessions()

    data = json.loads(service.memory_file.read_text(encoding="utf-8"))

    assert data == {
        "session_1": [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Salut"},
        ]
    }


def test_build_prompt_messages_returns_structured_recent_messages(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Je veux une expo")
    service.append_message("session_1", "assistant", "Voici une expo")

    messages = service.build_prompt_messages("session_1")

    assert messages == [
        {"role": "user", "content": "Je veux une expo"},
        {"role": "assistant", "content": "Voici une expo"},
    ]


def test_build_prompt_messages_applies_limit(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Q1")
    service.append_message("session_1", "assistant", "R1")
    service.append_message("session_1", "user", "Q2")

    messages = service.build_prompt_messages("session_1", max_messages=1)

    assert messages == [{"role": "user", "content": "Q2"}]


def test_get_session_size_returns_number_of_valid_messages(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    service.append_message("session_1", "user", "Bonjour")
    service.append_message("session_1", "assistant", "Salut")

    assert service.get_session_size("session_1") == 2


def test_get_stats_returns_expected_counts(tmp_path):
    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
        max_turns=3,
    )

    service.append_message("session_1", "user", "Bonjour")
    service.append_message("session_1", "assistant", "Salut")
    service.append_message("session_2", "user", "Expo ?")

    stats = service.get_stats()

    assert stats == {
        "n_sessions": 2,
        "n_messages": 3,
        "max_turns": 3,
        "max_messages": 6,
    }


def test_get_stats_ignores_invalid_sessions(tmp_path):
    memory_file = tmp_path / "memory.json"
    payload = {
        "session_1": [
            {"role": "user", "content": "Bonjour"},
        ],
        "session_2": [
            {"role": "system", "content": "invalid"},
            {"role": "user", "content": ""},
        ],
        "": [
            {"role": "user", "content": "hidden"},
        ],
    }
    memory_file.write_text(json.dumps(payload), encoding="utf-8")

    service = MemoryService(
        memory_dir=tmp_path,
        memory_file="memory.json",
    )

    stats = service.get_stats()

    assert stats["n_sessions"] == 1
    assert stats["n_messages"] == 1