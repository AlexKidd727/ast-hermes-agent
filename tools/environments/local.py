"""Local execution environment — spawn-per-call with session snapshot."""

import os
import logging
import platform
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from tools.environments.base import BaseEnvironment, _pipe_stdin

_IS_WINDOWS = platform.system() == "Windows"
logger = logging.getLogger(__name__)


def _pipe_stdin_binary(proc: subprocess.Popen, data: str) -> None:
    """Write *data* (encoded to UTF-8) to proc.stdin on a daemon thread.

    Used when the subprocess was opened in binary mode (no ``text=True``).
    """

    def _write():
        try:
            proc.stdin.write(data.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=_write, daemon=True).start()


def _git_bash_pwd_to_win32(path: str) -> str:
    """Convert Git Bash ``pwd -P`` output into a native Windows cwd path."""
    path = (path or "").strip()
    if not path or not _IS_WINDOWS:
        return path

    p = path.replace("\\", "/")
    low = p.lower()
    if low == "/tmp" or low.startswith("/tmp/"):
        win_tmp = os.path.normpath(tempfile.gettempdir())
        if low == "/tmp":
            return win_tmp
        tail = p[5:].lstrip("/")
        return os.path.normpath(os.path.join(win_tmp, tail)) if tail else win_tmp

    if len(p) >= 3 and p[0] == "/" and p[1].isalpha() and p[2] == "/":
        drive = p[1].upper()
        tail = p[3:].replace("/", "\\").rstrip("\\")
        return f"{drive}:\\{tail}" if tail else f"{drive}:\\"

    if low.startswith("/cygdrive/"):
        parts = [x for x in p.split("/") if x]
        if len(parts) >= 2 and parts[0].lower() == "cygdrive" and len(parts[1]) == 1:
            drive = parts[1].upper()
            tail = "\\".join(parts[2:])
            return f"{drive}:\\{tail}" if tail else f"{drive}:\\"

    return path


def _win32_to_git_bash_path(path: str) -> str:
    """Convert a native Windows path into a Git Bash-compatible path."""
    path = (path or "").strip()
    if not path or not _IS_WINDOWS:
        return path

    normalized = os.path.normpath(path)
    drive, tail = os.path.splitdrive(normalized)
    if drive and len(drive) == 2 and drive[1] == ":":
        drive_letter = drive[0].lower()
        tail = tail.replace("\\", "/").lstrip("/")
        return f"/{drive_letter}/{tail}" if tail else f"/{drive_letter}"
    return normalized.replace("\\", "/")


# Hermes-internal env vars that should NOT leak into terminal subprocesses.
_HERMES_PROVIDER_ENV_FORCE_PREFIX = "_HERMES_FORCE_"


def _build_provider_env_blocklist() -> frozenset:
    """Derive the blocklist from provider, tool, and gateway config."""
    blocked: set[str] = set()

    try:
        from hermes_cli.auth import PROVIDER_REGISTRY
        for pconfig in PROVIDER_REGISTRY.values():
            blocked.update(pconfig.api_key_env_vars)
            if pconfig.base_url_env_var:
                blocked.add(pconfig.base_url_env_var)
    except ImportError:
        pass

    try:
        from hermes_cli.config import OPTIONAL_ENV_VARS
        for name, metadata in OPTIONAL_ENV_VARS.items():
            category = metadata.get("category")
            if category in {"tool", "messaging"}:
                blocked.add(name)
            elif category == "setting" and metadata.get("password"):
                blocked.add(name)
    except ImportError:
        pass

    blocked.update({
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "LLM_MODEL",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "PERPLEXITY_API_KEY",
        "COHERE_API_KEY",
        "FIREWORKS_API_KEY",
        "XAI_API_KEY",
        "HELICONE_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "TELEGRAM_HOME_CHANNEL",
        "TELEGRAM_HOME_CHANNEL_NAME",
        "DISCORD_HOME_CHANNEL",
        "DISCORD_HOME_CHANNEL_NAME",
        "DISCORD_REQUIRE_MENTION",
        "DISCORD_FREE_RESPONSE_CHANNELS",
        "DISCORD_AUTO_THREAD",
        "SLACK_HOME_CHANNEL",
        "SLACK_HOME_CHANNEL_NAME",
        "SLACK_ALLOWED_USERS",
        "WHATSAPP_ENABLED",
        "WHATSAPP_MODE",
        "WHATSAPP_ALLOWED_USERS",
        "SIGNAL_HTTP_URL",
        "SIGNAL_ACCOUNT",
        "SIGNAL_ALLOWED_USERS",
        "SIGNAL_GROUP_ALLOWED_USERS",
        "SIGNAL_HOME_CHANNEL",
        "SIGNAL_HOME_CHANNEL_NAME",
        "SIGNAL_IGNORE_STORIES",
        "HASS_TOKEN",
        "HASS_URL",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
        "EMAIL_IMAP_HOST",
        "EMAIL_SMTP_HOST",
        "EMAIL_HOME_ADDRESS",
        "EMAIL_HOME_ADDRESS_NAME",
        "GATEWAY_ALLOWED_USERS",
        "GH_TOKEN",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_APP_INSTALLATION_ID",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "DAYTONA_API_KEY",
    })
    return frozenset(blocked)


_HERMES_PROVIDER_ENV_BLOCKLIST = _build_provider_env_blocklist()


def _sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    """Filter Hermes-managed secrets from a subprocess environment."""
    try:
        from tools.env_passthrough import is_env_passthrough as _is_passthrough
    except Exception:
        _is_passthrough = lambda _: False  # noqa: E731

    sanitized: dict[str, str] = {}

    for key, value in (base_env or {}).items():
        if key.startswith(_HERMES_PROVIDER_ENV_FORCE_PREFIX):
            continue
        if key not in _HERMES_PROVIDER_ENV_BLOCKLIST or _is_passthrough(key):
            sanitized[key] = value

    for key, value in (extra_env or {}).items():
        if key.startswith(_HERMES_PROVIDER_ENV_FORCE_PREFIX):
            real_key = key[len(_HERMES_PROVIDER_ENV_FORCE_PREFIX):]
            sanitized[real_key] = value
        elif key not in _HERMES_PROVIDER_ENV_BLOCKLIST or _is_passthrough(key):
            sanitized[key] = value

    # Per-profile HOME isolation for background processes (same as _make_run_env).
    from hermes_constants import get_subprocess_home
    _profile_home = get_subprocess_home()
    if _profile_home:
        sanitized["HOME"] = _profile_home

    return sanitized


def _find_bash() -> str | None:
    """Find bash for command execution. Returns None if not found on Windows."""
    if not _IS_WINDOWS:
        return (
            shutil.which("bash")
            or ("/usr/bin/bash" if os.path.isfile("/usr/bin/bash") else None)
            or ("/bin/bash" if os.path.isfile("/bin/bash") else None)
            or os.environ.get("SHELL")
            or "/bin/sh"
        )

    custom = os.environ.get("HERMES_GIT_BASH_PATH")
    if custom and os.path.isfile(custom):
        return custom

    found = shutil.which("bash")
    if found:
        return found

    for candidate in (
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate

    return None


def _find_powershell() -> str | None:
    """Find PowerShell on Windows. Tries pwsh.exe first, then powershell.exe."""
    for exe in ("pwsh.exe", "powershell.exe"):
        found = shutil.which(exe)
        if found:
            return found
    # Check common install locations
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(windir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(candidate):
        return candidate
    return None


_SHELL_INFO_CACHE: tuple[str, str] | None = None


def _get_shell_info() -> tuple[str, str]:
    """Return (shell_path, shell_type) for command execution.

    shell_type is one of: ``"bash"``, ``"cmd"``, ``"powershell"``.

    On non-Windows: always returns (bash_path, "bash").
    On Windows: respects ``HERMES_WINDOWS_SHELL`` env var, auto-detects otherwise.
    Auto-detection order: Git Bash → PowerShell → cmd.

    Result is cached after first call.
    """
    global _SHELL_INFO_CACHE
    if _SHELL_INFO_CACHE is not None:
        return _SHELL_INFO_CACHE

    if not _IS_WINDOWS:
        bash = _find_bash()
        if not bash:
            raise RuntimeError("No shell found on Unix system")
        _SHELL_INFO_CACHE = (bash, "bash")
        return _SHELL_INFO_CACHE

    preferred = os.environ.get("HERMES_WINDOWS_SHELL", "").strip().lower()

    if preferred in ("cmd", "command", "dos"):
        comspec = os.environ.get("COMSPEC", os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"))
        _SHELL_INFO_CACHE = (comspec, "cmd")
        return _SHELL_INFO_CACHE

    if preferred in ("powershell", "pwsh", "ps"):
        ps = _find_powershell()
        if ps:
            _SHELL_INFO_CACHE = (ps, "powershell")
            return _SHELL_INFO_CACHE
        # Fall back to cmd if PowerShell not found
        comspec = os.environ.get("COMSPEC", os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"))
        _SHELL_INFO_CACHE = (comspec, "cmd")
        return _SHELL_INFO_CACHE

    if preferred in ("bash", "gitbash", "git"):
        bash = _find_bash()
        if bash:
            _SHELL_INFO_CACHE = (bash, "bash")
            return _SHELL_INFO_CACHE
        # Fall back to auto-detect

    # Auto-detect: bash → powershell → cmd
    bash = _find_bash()
    if bash:
        _SHELL_INFO_CACHE = (bash, "bash")
        return _SHELL_INFO_CACHE

    ps = _find_powershell()
    if ps:
        _SHELL_INFO_CACHE = (ps, "powershell")
        return _SHELL_INFO_CACHE

    comspec = os.environ.get("COMSPEC", os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"))
    _SHELL_INFO_CACHE = (comspec, "cmd")
    return _SHELL_INFO_CACHE


def _resolve_shell() -> str:
    """Resolve shell path for command execution (raises if nothing found).

    This is the public entry point used by code that must have a working shell
    or fail with a clear error message.
    """
    path, _stype = _get_shell_info()
    if not path or not os.path.isfile(path):
        if _IS_WINDOWS:
            raise RuntimeError(
                "No command shell found on Windows.\n"
                "Install Git for Windows (https://git-scm.com/download/win) "
                "for full bash support, or ensure cmd.exe is available.\n"
                "Set HERMES_WINDOWS_SHELL=bash|cmd|powershell to choose."
            )
        raise RuntimeError("No shell found on Unix system")
    return path


# Backward compat — process_registry.py imports this name.
# Now returns the resolved shell path (which may be cmd.exe or powershell.exe on Windows).
_find_shell = _resolve_shell


# Standard PATH entries for environments with minimal PATH.
_SANE_PATH = (
    "/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def _is_windows_native_shell() -> bool:
    """Return True when the active shell is cmd.exe or powershell.exe (not bash)."""
    _, stype = _get_shell_info()
    return stype in ("cmd", "powershell")


def _make_run_env(env: dict) -> dict:
    """Build a run environment with a sane PATH and provider-var stripping."""
    try:
        from tools.env_passthrough import is_env_passthrough as _is_passthrough
    except Exception:
        _is_passthrough = lambda _: False  # noqa: E731

    merged = dict(os.environ | env)
    run_env = {}
    for k, v in merged.items():
        if k.startswith(_HERMES_PROVIDER_ENV_FORCE_PREFIX):
            real_key = k[len(_HERMES_PROVIDER_ENV_FORCE_PREFIX):]
            run_env[real_key] = v
        elif k not in _HERMES_PROVIDER_ENV_BLOCKLIST or _is_passthrough(k):
            run_env[k] = v

    # Only append Unix PATH entries on non-Windows systems.
    # On Windows, PATH uses ';' as a separator and Unix paths like
    # /usr/bin are meaningless — mixing ':' into a ';'-separated PATH
    # breaks command resolution.
    if not _IS_WINDOWS:
        existing_path = run_env.get("PATH", "")
        if "/usr/bin" not in existing_path.split(":"):
            run_env["PATH"] = f"{existing_path}:{_SANE_PATH}" if existing_path else _SANE_PATH

    # Per-profile HOME isolation: redirect system tool configs (git, ssh, gh,
    # npm …) into {HERMES_HOME}/home/ when that directory exists.  Only the
    # subprocess sees the override — the Python process keeps the real HOME.
    from hermes_constants import get_subprocess_home
    _profile_home = get_subprocess_home()
    if _profile_home:
        run_env["HOME"] = _profile_home

    return run_env


def _read_terminal_shell_init_config() -> tuple[list[str], bool]:
    """Return (shell_init_files, auto_source_bashrc) from config.yaml.

    Best-effort — returns sensible defaults on any failure so terminal
    execution never breaks because the config file is unreadable.
    """
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        terminal_cfg = cfg.get("terminal") or {}
        files = terminal_cfg.get("shell_init_files") or []
        if not isinstance(files, list):
            files = []
        auto_bashrc = bool(terminal_cfg.get("auto_source_bashrc", True))
        return [str(f) for f in files if f], auto_bashrc
    except Exception:
        return [], True


def _expand_shell_init_candidate(raw: str) -> str:
    """Expand env vars and ``~`` for shell init paths.

    On Windows we prefer ``HOME`` for ``~/...`` because local execution uses
    Git Bash semantics and many tests point HOME at a temp profile root.
    """
    text = os.path.expandvars(str(raw).strip())
    if text.startswith("~") and (len(text) == 1 or text[1] in "/\\"):
        win_home = os.environ.get("HOME") if os.name == "nt" else None
        if win_home:
            tail = "" if text == "~" else text[2:].lstrip("/\\")
            base = os.path.normpath(os.path.expandvars(win_home))
            return base if not tail else os.path.normpath(os.path.join(base, tail))
    try:
        return os.path.normpath(os.path.expanduser(text))
    except Exception:
        return ""


def _resolve_shell_init_files() -> list[str]:
    """Resolve the list of files to source before the login-shell snapshot.

    Expands ``~`` and ``${VAR}`` references and drops anything that doesn't
    exist on disk, so a missing ``~/.bashrc`` never breaks the snapshot.
    The ``auto_source_bashrc`` path runs only when the user hasn't supplied
    an explicit list — once they have, Hermes trusts them.
    """
    explicit, auto_bashrc = _read_terminal_shell_init_config()

    candidates: list[str] = []
    if explicit:
        candidates.extend(explicit)
    elif auto_bashrc:
        # Build a login-shell-ish source list so tools like n / nvm / asdf /
        # pyenv that self-install into the user's shell rc land on PATH in
        # the captured snapshot.
        #
        # ~/.profile and ~/.bash_profile run first because they have no
        # interactivity guard — installers like ``n`` and ``nvm`` append
        # their PATH export there on most distros, and a non-interactive
        # ``. ~/.profile`` picks that up.
        #
        # ~/.bashrc runs last. On Debian/Ubuntu the default bashrc starts
        # with ``case $- in *i*) ;; *) return;; esac`` and exits early
        # when sourced non-interactively, which is why sourcing bashrc
        # alone misses nvm/n PATH additions placed below that guard. We
        # still include it so users who put PATH logic in bashrc (and
        # stripped the guard, or never had one) keep working.
        candidates.extend(["~/.profile", "~/.bash_profile", "~/.bashrc"])

    resolved: list[str] = []
    for raw in candidates:
        path = _expand_shell_init_candidate(raw)
        if path and os.path.isfile(path):
            resolved.append(path)
    return resolved


def _prepend_shell_init(cmd_string: str, files: list[str]) -> str:
    """Prepend ``source <file>`` lines (guarded + silent) to a bash script.

    Each file is wrapped so a failing rc file doesn't abort the whole
    bootstrap: ``set +e`` keeps going on errors, ``2>/dev/null`` hides
    noisy prompts, and ``|| true`` neutralises the exit status.
    """
    if not files:
        return cmd_string

    prelude_parts = ["set +e"]
    for path in files:
        # shlex.quote isn't available here without an import; the files list
        # comes from os.path.expanduser output so it's a concrete absolute
        # path.  Escape single quotes defensively anyway.
        safe = path.replace("'", "'\\''")
        prelude_parts.append(f"[ -r '{safe}' ] && . '{safe}' 2>/dev/null || true")
    prelude = "\n".join(prelude_parts) + "\n"
    return prelude + cmd_string


class LocalEnvironment(BaseEnvironment):
    """Run commands directly on the host machine.

    Spawn-per-call: every execute() spawns a fresh shell process.
    Session snapshot preserves env vars across calls.
    CWD persists via file-based read after each command.

    On Windows, supports three shell flavours via auto-detection or
    ``HERMES_WINDOWS_SHELL``: ``bash`` (Git for Windows), ``cmd``, and
    ``powershell``.
    """

    _shell_type: str = "bash"  # set in __init__

    def __init__(self, cwd: str = "", timeout: int = 60, env: dict = None):
        super().__init__(cwd=cwd or os.getcwd(), timeout=timeout, env=env)
        self._shell_path, self._shell_type = _get_shell_info()
        # On Windows, always store the native Windows path so that Python
        # file operations (open, os.path, …) work without translation.
        # Git Bash paths are computed on the fly when needed via
        # _win32_to_git_bash_path().
        self.host_cwd = os.path.abspath(cwd or os.getcwd()) if _IS_WINDOWS else self.cwd
        if _IS_WINDOWS:
            self.cwd = self.host_cwd
        self.init_session()

    def get_temp_dir(self) -> str:
        """Return a shell-safe writable temp dir for local execution.

        On Windows (any shell): returns the native Windows temp directory
        (``%TEMP%``) so that Python ``open()`` calls and subprocess writes
        target the same location.  Git Bash path translation is applied on
        the fly inside ``_wrap_command()`` when building the bash script.

        On Unix: prefers ``TMPDIR``, falls back to ``/tmp``.
        """
        if _IS_WINDOWS:
            candidate = os.environ.get("TEMP") or os.environ.get("TMP")
            if candidate:
                return candidate.rstrip("\\") or candidate
            return tempfile.gettempdir().rstrip("\\") or tempfile.gettempdir()

        # Unix path
        for env_var in ("TMPDIR", "TMP", "TEMP"):
            candidate = self.env.get(env_var) or os.environ.get(env_var)
            if candidate and candidate.startswith("/"):
                return candidate.rstrip("/") or "/"

        if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK):
            return "/tmp"

        candidate = tempfile.gettempdir()
        if candidate.startswith("/"):
            return candidate.rstrip("/") or "/"

        return "/tmp"

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        shell_path = self._shell_path
        shell_type = self._shell_type

        if shell_type == "bash":
            return self._run_bash_impl(cmd_string, login=login, timeout=timeout,
                                       stdin_data=stdin_data)
        elif shell_type == "cmd":
            return self._run_cmd(cmd_string, timeout=timeout, stdin_data=stdin_data)
        elif shell_type == "powershell":
            return self._run_powershell(cmd_string, timeout=timeout, stdin_data=stdin_data)
        else:
            raise RuntimeError(f"Unknown shell type: {shell_type}")

    def _run_bash_impl(self, cmd_string: str, *, login: bool = False,
                       timeout: int = 120,
                       stdin_data: str | None = None) -> subprocess.Popen:
        bash = self._shell_path
        # For login-shell invocations (used by init_session to build the
        # environment snapshot), prepend sources for the user's bashrc /
        # custom init files so tools registered outside bash_profile
        # (nvm, asdf, pyenv, …) end up on PATH in the captured snapshot.
        # Non-login invocations are already sourcing the snapshot and
        # don't need this.
        if login:
            init_files = _resolve_shell_init_files()
            if init_files:
                cmd_string = _prepend_shell_init(cmd_string, init_files)
        args = [bash, "-l", "-c", cmd_string] if login else [bash, "-c", cmd_string]
        run_env = _make_run_env(self.env)

        proc = subprocess.Popen(
            args,
            text=True,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
            cwd=self.host_cwd if _IS_WINDOWS else self.cwd,
        )

        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)

        return proc

    def _run_cmd(self, cmd_string: str, *, timeout: int = 120,
                 stdin_data: str | None = None) -> subprocess.Popen:
        """Run a command via cmd.exe on Windows.

        Uses binary mode (no ``text=True``) so that ``_wait_for_process`` can
        read raw bytes from the pipe fd via ``os.read()`` without conflicting
        with ``TextIOWrapper`` internal buffering.
        """
        args = [self._shell_path, "/V:ON", "/c", cmd_string]
        run_env = _make_run_env(self.env)

        proc = subprocess.Popen(
            args,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            cwd=self.host_cwd,
        )

        if stdin_data is not None:
            _pipe_stdin_binary(proc, stdin_data)

        return proc

    def _run_powershell(self, cmd_string: str, *, timeout: int = 120,
                        stdin_data: str | None = None) -> subprocess.Popen:
        """Run a command via powershell.exe on Windows.

        Uses binary mode for the same reason as ``_run_cmd``.
        """
        args = [self._shell_path, "-NoProfile", "-Command", cmd_string]
        run_env = _make_run_env(self.env)

        proc = subprocess.Popen(
            args,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            cwd=self.host_cwd,
        )

        if stdin_data is not None:
            _pipe_stdin_binary(proc, stdin_data)

        return proc

    def init_session(self):
        """Capture login shell environment into a snapshot file.

        For bash on Windows: rewrites temp-file paths in the bootstrap script
        from native Windows format (``C:\\Temp\\...``) to Git Bash format
        (``/c/Temp/...``) so bash can access them.

        For cmd/powershell: no snapshot — we always use login-style invocation.
        """
        if self._shell_type != "bash":
            self._snapshot_ready = False
            logger.info("Session snapshot skipped for shell type %s (session=%s)",
                        self._shell_type, self._session_id)
            return

        if _IS_WINDOWS:
            # Build the bootstrap ourselves so we can translate paths.
            snap_gb = _win32_to_git_bash_path(self._snapshot_path)
            cwd_file_gb = _win32_to_git_bash_path(self._cwd_file)
            bootstrap = (
                f"export -p > {snap_gb}\n"
                f"declare -f | grep -vE '^_[^_]' >> {snap_gb}\n"
                f"alias -p >> {snap_gb}\n"
                f"echo 'shopt -s expand_aliases' >> {snap_gb}\n"
                f"echo 'set +e' >> {snap_gb}\n"
                f"echo 'set +u' >> {snap_gb}\n"
                f"pwd -P > {cwd_file_gb} 2>/dev/null || true\n"
                f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"\n"
            )
            try:
                proc = self._run_bash_impl(bootstrap, login=True, timeout=self._snapshot_timeout)
                result = self._wait_for_process(proc, timeout=self._snapshot_timeout)
                self._snapshot_ready = True
                self._update_cwd(result)
                logger.info(
                    "Session snapshot created (session=%s, cwd=%s)",
                    self._session_id,
                    self.cwd,
                )
            except Exception as exc:
                logger.warning(
                    "init_session failed (session=%s): %s — "
                    "falling back to bash -l per command",
                    self._session_id,
                    exc,
                )
                self._snapshot_ready = False
        else:
            super().init_session()

    def _wait_for_process(self, proc, timeout: int = 120) -> dict:
        """Wait for a subprocess to complete and drain its output.

        Overrides the base-class implementation to avoid ``select.select()``,
        which does not work with pipe handles on some Windows / Python
        combinations.  Uses a blocking-reader thread instead.
        """
        if not _IS_WINDOWS:
            return super()._wait_for_process(proc, timeout=timeout)
        return self._wait_for_process_threaded(proc, timeout=timeout)

    def _wait_for_process_threaded(self, proc, timeout: int = 120) -> dict:
        """Wait for *proc* using a blocking-reader thread (no select)."""
        import codecs
        output_chunks: list[str] = []

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def _drain():
            try:
                while True:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output_chunks.append(decoder.decode(chunk))
            except (ValueError, OSError):
                pass
            finally:
                try:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        output_chunks.append(tail)
                except Exception:
                    pass

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()
        deadline = time.monotonic() + timeout

        while proc.poll() is None:
            if time.monotonic() > deadline:
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                partial = "".join(output_chunks)
                return {
                    "output": partial + f"\n[Command timed out after {timeout}s]"
                    if partial
                    else f"[Command timed out after {timeout}s]",
                    "returncode": 124,
                }
            # Check for interrupt (imported from shared module)
            from tools.interrupt import is_interrupted as _is_interrupted
            if _is_interrupted():
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                return {
                    "output": "".join(output_chunks) + "\n[Command interrupted]",
                    "returncode": 130,
                }
            time.sleep(0.2)

        # Process exited — give the drain thread a moment to catch final bytes
        drain_thread.join(timeout=2)
        return {
            "output": "".join(output_chunks),
            "returncode": proc.returncode or 0,
        }

    def _wrap_command(self, command: str, cwd: str) -> str:
        """Build the shell script that runs the command and emits CWD markers.

        Delegates to the appropriate builder depending on the active shell type.
        For bash on Windows: converts paths inside the generated script from
        native Windows format to Git Bash format so that bash can access
        the _cwd_file and _snapshot_path.
        """
        if self._shell_type == "bash":
            if _IS_WINDOWS:
                # Convert cwd and temp-file paths to Git Bash format for
                # the bash script body.  Python stores native Windows paths
                # so that ``open()`` works; the bash script needs Git Bash
                # translations of those same locations.
                cwd_gb = _win32_to_git_bash_path(cwd)
                cwd_file_gb = _win32_to_git_bash_path(self._cwd_file)
                snap_gb = _win32_to_git_bash_path(self._snapshot_path)
                wrapped = super()._wrap_command(command, cwd_gb)
                # The base _wrap_command embeds _snapshot_path and _cwd_file
                # literally.  Replace the Windows paths with Git Bash versions.
                wrapped = wrapped.replace(self._snapshot_path, snap_gb)
                wrapped = wrapped.replace(self._cwd_file, cwd_file_gb)
                return wrapped
            else:
                return super()._wrap_command(command, cwd)
        elif self._shell_type == "cmd":
            return self._wrap_command_cmd(command, cwd)
        elif self._shell_type == "powershell":
            return self._wrap_command_powershell(command, cwd)
        else:
            return super()._wrap_command(command, cwd)

    def _wrap_command_cmd(self, command: str, cwd: str) -> str:
        """Build a cmd.exe one-liner wrapping *command* with CWD tracking.

        Uses ``&&`` / ``&`` separators because ``cmd /c`` does not accept
        multi-line scripts.  Delayed expansion (``!VAR!``) captures the real
        exit code of the user command rather than the parse-time value of
        ``%ERRORLEVEL%``.

        Pattern::

            @echo off && cd /d <cwd> && <command> & set EC=!ERRORLEVEL! && echo !cd! > <file> 2>NUL && echo <marker>!cd!<marker> && exit !EC!
        """
        marker = self._cwd_marker
        escaped_cmd = command.replace("%", "%%")  # escape percent signs for cmd
        escaped_cwd_file = self._cwd_file

        return (
            f"@echo off && "
            f"cd /d {cwd} && "
            f"{escaped_cmd} & "
            f"set EC=!ERRORLEVEL! && "
            f"echo !cd! > {escaped_cwd_file} 2>NUL && "
            f"echo {marker}!cd!{marker} && "
            f"exit !EC!"
        )

    def _wrap_command_powershell(self, command: str, cwd: str) -> str:
        """Build a PowerShell script wrapping *command* with CWD tracking."""
        marker = self._cwd_marker
        # PowerShell quoting: escape single quotes
        escaped_cmd = command.replace("'", "''")
        escaped_cwd_file = self._cwd_file.replace("'", "''")
        escaped_cwd = cwd.replace("'", "''")

        parts = [
            "$env:PYTHONUNBUFFERED = '1'",
            f"Set-Location -LiteralPath '{escaped_cwd}' 2>$null",
            f"Invoke-Expression '{escaped_cmd}'",
            "$ec = $LASTEXITCODE",
            f"$cwd = (Get-Location).Path",
            f"$cwd | Out-File -FilePath '{escaped_cwd_file}' -NoNewline -ErrorAction SilentlyContinue",
            f"Write-Output '{marker}' + $cwd + '{marker}'",
            f"exit $ec",
        ]
        return "\n".join(parts)

    def _update_cwd(self, result: dict):
        """Read CWD from temp file or output marker.

        For bash: reads from temp file and strips marker from output.
        For cmd/powershell: reads from temp file, strips marker from output,
        and normalises line endings.

        NOTE: ``_extract_cwd_from_output`` overwrites ``self.cwd`` with the
        raw marker value (a Git Bash path on Windows).  We re-apply the
        Git-Bash→Windows conversion afterwards so ``self.cwd`` stays a
        native Windows path that Python ``open()`` etc. can use directly.
        """
        # Read CWD from temp file
        try:
            raw = open(self._cwd_file).read().strip()
            if raw:
                if _IS_WINDOWS:
                    if self._shell_type == "bash":
                        raw = _git_bash_pwd_to_win32(raw)
                    self.cwd = raw
                    self.host_cwd = raw
                else:
                    self.cwd = raw
                    self.host_cwd = raw
        except (OSError, FileNotFoundError):
            pass

        # Strip the marker from output so it's not visible to the model.
        # WARNING: _extract_cwd_from_output overwrites self.cwd from the
        # CWD marker embedded in stdout.  On Windows with bash the marker
        # contains a Git Bash path — we convert it back below.
        self._extract_cwd_from_output(result)

        # _extract_cwd_from_output may have overwritten self.cwd with a
        # Git Bash path from the output marker.  Convert it to a native
        # Windows path so that Python file operations work.
        if _IS_WINDOWS and self._shell_type == "bash":
            self.cwd = _git_bash_pwd_to_win32(self.cwd)
            # Sync host_cwd (it may already be correct, but be defensive)
            if self.host_cwd != self.cwd:
                # host_cwd might have been set from the file (correct) or
                # from the marker (wrong).  Unify on the Windows version.
                self.host_cwd = self.cwd

        # Normalise Windows line endings for native shells
        if _is_windows_native_shell():
            output = result.get("output") or ""
            result["output"] = output.replace("\r\n", "\n").replace("\r", "\n")

    def _kill_process(self, proc):
        """Kill the entire process group (all children)."""
        try:
            if _IS_WINDOWS:
                proc.terminate()
            else:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass

    def cleanup(self):
        """Clean up temp files."""
        for f in (self._snapshot_path, self._cwd_file):
            try:
                os.unlink(f)
            except OSError:
                pass
