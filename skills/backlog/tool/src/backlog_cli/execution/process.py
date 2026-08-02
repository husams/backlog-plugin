"""Bounded process IO, matching, and output redaction."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading

from .contracts import ShellSpec, TextMatcher

def _communicate_bounded(process: subprocess.Popen, timeout: int,
                         limit: int) -> tuple[str, str, bool, bool, str]:
    captured = [bytearray(), bytearray()]
    truncated = [False]
    read_errors: list[str] = []
    lock = threading.Lock()

    def drain(stream, index: int) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, limit - sum(len(value) for value in captured))
                    captured[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated[0] = True
        except OSError as exc:
            read_errors.append(exc.__class__.__name__)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
    for thread in threads:
        thread.join()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    stdout = captured[0].decode("utf-8", "replace")
    stderr = captured[1].decode("utf-8", "replace")
    diagnostic = ",".join(read_errors)
    return stdout, stderr, truncated[0], timed_out, diagnostic


def _mismatches(shell: ShellSpec, exit_code: int | None,
                stdout: str, stderr: str) -> list[str]:
    mismatches = []
    if exit_code != shell.expected_exit_code:
        mismatches.append(
            f"exit_code_mismatch:expected={shell.expected_exit_code},actual={exit_code}"
        )
    for label, matcher, actual in (
        ("stdout", shell.stdout, stdout), ("stderr", shell.stderr, stderr)
    ):
        if matcher and not _matches(matcher, actual):
            mismatches.append(f"{label}_mismatch")
    return mismatches


def _matches(matcher: TextMatcher, actual: str) -> bool:
    if matcher.equals is not None:
        return actual == matcher.equals
    if matcher.contains is not None:
        return matcher.contains in actual
    assert matcher.regex is not None
    return re.search(matcher.regex, actual) is not None


def _redact(value: str, secrets) -> str:
    for secret in sorted(
        {secret for secret in secrets if secret}, key=len, reverse=True
    ):
        value = value.replace(secret, "[REDACTED]")
    return value
