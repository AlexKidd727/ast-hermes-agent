# Plan: Fix Gateway Crash Shutdowns & Improve Exit Reason Visibility

## Analysis Summary

After studying `gateway/run.py` (11152 lines), `hermes_cli/gateway.py` (173KB), `gateway/status.py`, and `tui_gateway/`, I found these root causes:

### Problems

1. **No global exception handlers** — `sys.excepthook` and `loop.set_exception_handler()` are not installed. Unhandled exceptions in async tasks become "Task exception was never retrieved" and the process hangs in an undefined state without logging why.

2. **`start_gateway()` has no try/except** — The main lifecycle (`runner.start()` → `wait_for_shutdown()`) is wrapped only in `asyncio.run()`. Any exception propagates directly to the CLI, which does `sys.exit(1)` without capturing or displaying the reason.

3. **Exit reasons only logged to file** — `runner.exit_reason` is only written via `logger.error()` to `gateway.log`. The CLI shows a generic "Gateway exited unsuccessfully" message and tells the user to check log files.

4. **Signal handler creates orphaned task** — `shutdown_signal_handler()` calls `asyncio.create_task(runner.stop())` without storing the task reference. Multiple signals could create concurrent `stop()` coroutines.

5. **`wait_for_shutdown()` is cancellation-unsafe** — `await self._shutdown_event.wait()` with no `try/except` for `CancelledError`.

6. **CLI `run_gateway()` hides exit reason** — On failure it prints log paths but not the actual reason stored in the runner.

## Changes

### 1. `gateway/run.py` — Add global exception handlers (lines ~10800-10900)

- Install `sys.excepthook` to write unhandled exceptions to a crash log + stderr
- Install `loop.set_exception_handler()` for asyncio task exceptions
- Write to `{HERMES_HOME}/gateway_crash.log`

### 2. `gateway/run.py` — Wrap `start_gateway()` body in try/except (lines ~10820-10900)

- Catch `BaseException` around the main lifecycle
- Log full traceback and set exit reason
- Print reason to stderr before returning

### 3. `gateway/run.py` — Print exit reason to stderr on shutdown (lines ~11090-11110)

- After `wait_for_shutdown()`, print the exit reason to `sys.stderr`
- Include both clean and failure exit reasons

### 4. `gateway/run.py` — Fix signal handler (lines ~10965-11030)

- Track the stop task: `self._stop_task`
- Guard against concurrent stop() calls in the signal handler

### 5. `hermes_cli/gateway.py` — Improve CLI error output (lines ~2364-2380)

- When `start_gateway()` returns False, include exit reason in the printed message
- Read exit reason from runner state if possible, or from the crash log
