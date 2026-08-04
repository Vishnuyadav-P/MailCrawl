from scripts.run_and_export import is_terminal_status


def test_terminal_statuses() -> None:
    assert is_terminal_status("completed")
    assert is_terminal_status("failed")
    assert is_terminal_status("interrupted")
    assert not is_terminal_status("running")
    assert not is_terminal_status("pending")
