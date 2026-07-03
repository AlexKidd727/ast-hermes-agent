import json

from tools import file_tools


def test_windows_sandbox_path_hint_for_testbed(monkeypatch):
    monkeypatch.setattr(file_tools.os, "name", "nt", raising=False)
    monkeypatch.setenv("TERMINAL_CWD", r"D:\Python\LLM\HermesAgent\hermes-agent")

    hint = file_tools._get_windows_sandbox_path_hint("/testbed/RESIZE-FIX.MD")

    assert hint is not None
    assert "/testbed/RESIZE-FIX.MD" in hint
    assert r"D:\Python\LLM\HermesAgent\hermes-agent" in hint


def test_windows_sandbox_path_hint_ignored_for_relative_path(monkeypatch):
    monkeypatch.setattr(file_tools.os, "name", "nt", raising=False)

    assert file_tools._get_windows_sandbox_path_hint("RESIZE-FIX.MD") is None


def test_search_tool_returns_hint_for_fake_sandbox_path(monkeypatch):
    monkeypatch.setattr(file_tools.os, "name", "nt", raising=False)
    monkeypatch.setenv("TERMINAL_CWD", r"D:\Python\LLM\HermesAgent\hermes-agent")

    result = json.loads(
        file_tools.search_tool("resize", path="/workspace/project", task_id="default")
    )

    assert "error" in result
    assert "Windows local filesystem" in result["error"]
