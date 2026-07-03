import os

from tools.environments import local as local_env
from tools import terminal_tool


def test_git_bash_pwd_to_win32_drive_path(monkeypatch):
    monkeypatch.setattr(local_env, "_IS_WINDOWS", True)
    assert local_env._git_bash_pwd_to_win32("/d/work/hermes-agent") == r"D:\work\hermes-agent"


def test_git_bash_pwd_to_win32_tmp_path(monkeypatch):
    monkeypatch.setattr(local_env, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_env.tempfile, "gettempdir", lambda: r"C:\Temp")
    assert local_env._git_bash_pwd_to_win32("/tmp/hermes/cwd") == r"C:\Temp\hermes\cwd"


def test_win32_to_git_bash_path(monkeypatch):
    monkeypatch.setattr(local_env, "_IS_WINDOWS", True)
    assert local_env._win32_to_git_bash_path(r"D:\work\hermes-agent") == "/d/work/hermes-agent"


def test_expand_shell_init_candidate_prefers_home_on_windows(monkeypatch, tmp_path):
    home = tmp_path / "home"
    profile = tmp_path / "userprofile"
    home.mkdir()
    profile.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setattr(local_env.os, "name", "nt", raising=False)

    resolved = local_env._expand_shell_init_candidate("~/custom.sh")

    assert resolved == os.path.normpath(str(home / "custom.sh"))


def test_normalize_windows_git_bash_exploration_command(monkeypatch):
    monkeypatch.setattr(terminal_tool.os, "name", "nt", raising=False)

    assert (
        terminal_tool._normalize_windows_git_bash_exploration_command("ls -la", "local")
        == "ls -1 | head -200"
    )
    assert (
        terminal_tool._normalize_windows_git_bash_exploration_command("ls -R", "local")
        == "find . -maxdepth 3 -print | head -200"
    )
    assert (
        terminal_tool._normalize_windows_git_bash_exploration_command("git status", "local")
        == "git status"
    )
