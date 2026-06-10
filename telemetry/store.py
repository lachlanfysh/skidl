"""JSONL run store: atomic appends, tolerant reads, run sessions.

Data integrity rules:
- One record per line, written with a single os.write() syscall on an
  O_APPEND fd — atomic for our record sizes on a local filesystem, so
  concurrent processes never interleave partial lines.
- The reader is tolerant: a corrupt line costs one record, never the file.
- session() guarantees a record is written even when the body raises.

Pure stdlib + pydantic. No transport- or host-specific imports.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

from pydantic import ValidationError

from telemetry.models import DEFAULT_STATUS, RunRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent


def default_runs_path() -> Path:
    """Default store path; honours SKIDL_TELEMETRY_DIR at call time."""
    env_dir = os.environ.get("SKIDL_TELEMETRY_DIR")
    if env_dir:
        return Path(env_dir) / "runs.jsonl"
    return _REPO_ROOT / "telemetry" / "runs.jsonl"


RUNS_PATH = default_runs_path()


def atomic_append(path: Union[str, Path], line: str) -> None:
    """Append one line to *path* with a single write syscall, then fsync.

    O_APPEND + one os.write of the whole payload means concurrent writers
    on a local fs cannot interleave within a record.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError(
                f"short write to {path}: {written}/{len(payload)} bytes"
            )
        os.fsync(fd)
    finally:
        os.close(fd)


def read_records(path: Union[str, Path, None] = None) -> list[RunRecord]:
    """Read all valid RunRecords from a JSONL store.

    Unparsable lines are skipped with a warning on stderr — one corrupt
    line never poisons the dataset.
    """
    path = Path(path) if path is not None else default_runs_path()
    if not path.exists():
        return []
    records: list[RunRecord] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(RunRecord.model_validate_json(line))
            except (ValidationError, ValueError) as e:
                print(
                    f"telemetry: skipping unparsable line {lineno} in {path}: "
                    f"{type(e).__name__}",
                    file=sys.stderr,
                )
    return records


def _git_sha() -> str:
    """Short git SHA of the repo this module lives in, or '' on any failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def session(
    board_id: str,
    mode: str,
    run_id: Optional[str] = None,
    path: Union[str, Path, None] = None,
    **fields,
) -> Iterator[RunRecord]:
    """Run-record lifecycle: create, yield for filling, always persist.

    The record is appended to the store in all cases — clean exit, body
    exception, even KeyboardInterrupt. If an exception escapes the body
    and the caller never set an explicit status, the record is stamped
    status="crashed" with the exception as failure_reason, and the
    exception is re-raised after the write.
    """
    if "git_sha" not in fields:
        fields["git_sha"] = _git_sha()
    record = RunRecord(
        run_id=run_id or uuid.uuid4().hex[:12],
        board_id=board_id,
        mode=mode,
        started_at=_now_iso(),
        **fields,
    )
    target = Path(path) if path is not None else default_runs_path()
    error: Optional[BaseException] = None
    try:
        yield record
    except BaseException as e:
        error = e
    finally:
        record.finished_at = _now_iso()
        if error is not None and record.status == DEFAULT_STATUS:
            record.status = "crashed"
            record.failure_reason = f"{type(error).__name__}: {error}"
        record.finalize()
        atomic_append(target, record.model_dump_json())
    if error is not None:
        raise error
