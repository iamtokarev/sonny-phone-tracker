"""One Sonny at a time: a second `uv run sonny` refuses to start while one runs (two processes share the robot,
and an old one keeps running old code)."""

import os

from sonny.main import claim_instance


def test_a_second_instance_is_refused_and_told_which_process_runs(tmp_path):
    first = claim_instance(tmp_path)
    assert first is not None
    assert claim_instance(tmp_path) is None
    assert (tmp_path / "sonny.lock").read_text().strip() == str(os.getpid())
    first.close()


def test_the_lock_frees_when_the_first_instance_ends(tmp_path):
    claim_instance(tmp_path).close()
    second = claim_instance(tmp_path)
    assert second is not None
    second.close()
