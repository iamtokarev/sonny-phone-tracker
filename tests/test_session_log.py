import json

import pytest

from sonny.events import EndReason, Event, EventType
from sonny.session_log import SessionLog


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_session_file_gets_typed_timestamped_records(tmp_path):
    log = SessionLog(tmp_path)
    writer = log.start_session()
    writer.write("tool_call", name="start_session", args={"task": "thesis"})
    writer.write_event(Event(EventType.WAKE, {"score": 0.93}))
    [rec1, rec2] = records(writer.path)
    assert rec1["type"] == "tool_call" and rec1["args"] == {"task": "thesis"}
    assert isinstance(rec1["ts"], float)
    assert rec2 == {"ts": rec2["ts"], "type": "wake", "score": 0.93}


def test_one_file_per_session(tmp_path):
    log = SessionLog(tmp_path)
    first = log.start_session()
    log.end_session(EndReason.COMPLETED)
    second = log.start_session()
    assert first.path != second.path
    assert first.path.parent == second.path.parent == tmp_path


def test_idle_conversations_share_one_appended_file(tmp_path):
    SessionLog(tmp_path).idle.write("wake", score=0.9)
    SessionLog(tmp_path).idle.write("wake", score=0.95)
    idle = tmp_path / "idle.jsonl"
    assert [r["score"] for r in records(idle)] == [0.9, 0.95]


def test_end_session_logs_reason_and_closes(tmp_path):
    log = SessionLog(tmp_path)
    writer = log.start_session()
    log.end_session(EndReason.APP_QUIT)
    assert records(writer.path)[-1]["type"] == "session.ended"
    assert records(writer.path)[-1]["reason"] == "app_quit"
    assert log.session is None


def test_end_session_without_a_session_is_a_no_op(tmp_path):
    log = SessionLog(tmp_path)
    log.end_session(EndReason.APP_QUIT)
    assert list(tmp_path.glob("session-*")) == []


def test_starting_a_session_while_one_is_open_is_an_error(tmp_path):
    log = SessionLog(tmp_path)
    log.start_session()
    with pytest.raises(RuntimeError):
        log.start_session()


def test_log_dir_is_created(tmp_path):
    log = SessionLog(tmp_path / "nested" / "logs")
    log.idle.write("wake", score=1.0)
    assert (tmp_path / "nested" / "logs" / "idle.jsonl").exists()


def test_a_session_ended_with_its_file_kept_open_logs_one_end(tmp_path):
    log = SessionLog(tmp_path)
    writer = log.start_session()
    log.end_session(EndReason.COMPLETED, keep_open=True)  # time's up: an extend may still follow
    writer.write("tool_call", name="extend")
    log.end_session(EndReason.APP_QUIT)
    assert [(r["type"], r.get("reason")) for r in records(writer.path)] == [
        ("session.ended", "completed"),
        ("tool_call", None),
    ]
    assert log.session is None
