#!/usr/bin/env python
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

FILE_LIST = "/tmp/test_files.txt"
IMPORT_FAILED_PREFIX = "IMPORT_FAILED: "
TMP_REPORT_TEMPLATE = "/tmp/pf_report_{tid}.jsonl"

PER_FILE_CWD = os.environ.get("PER_FILE_CWD", "").rstrip("/") or None
RETRY_TIMEOUT = max(1, int(os.environ.get("RETRY_TIMEOUT", "300")))
EXEC_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT", "0"))
RETRY_WORKERS = max(1, int(os.environ.get("RETRY_WORKERS", "1")))

NODE_TIMEOUT = 30  # per-test wall-clock cap, kills hangs without stalling the file

# Target package's plugins can't fight; we must re-add the ones we still need.
AUTOLOAD_OFF = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1"

TIMEOUT_ARGS = ["--timeout", str(NODE_TIMEOUT), "--timeout-method=signal"]
if AUTOLOAD_OFF:
    TIMEOUT_ARGS = ["-p", "pytest_timeout", *TIMEOUT_ARGS]

# Plugins many suites use as fixtures; re-enable per-image only if installed.
OPT_IN_PLUGINS = ["pytest_mock", "pytest_asyncio", "pytest_env"]
EXTRA_PLUGIN_ARGS: list[str] = []
if AUTOLOAD_OFF:
    for name in OPT_IN_PLUGINS:
        if importlib.util.find_spec(name) is not None:
            EXTRA_PLUGIN_ARGS += ["-p", name]


def to_nodeid(path: str) -> str:
    p = path.replace("\\", "/")
    if PER_FILE_CWD and p.startswith(PER_FILE_CWD + "/"):
        return p[len(PER_FILE_CWD) + 1:]
    return p


def error_entry(path: str, longrepr: str) -> dict:
    return {"nodeid": to_nodeid(path), "outcome": "error", "duration": 0.0, "longrepr": longrepr}


def decode(b) -> str:
    if isinstance(b, (bytes, bytearray)):
        return b.decode("utf-8", "replace")
    return b or ""


def run_pytest(paths: list[str], timeout: int, extra_args: list[str]) -> tuple[list[dict], list[str], str]:
    tmp_report = Path(TMP_REPORT_TEMPLATE.format(tid=threading.get_ident()))
    tmp_report.unlink(missing_ok=True)
    cmd = [
        "pytest", *paths, "-v",
        "--continue-on-collection-errors",
        *TIMEOUT_ARGS,
        *EXTRA_PLUGIN_ARGS,
        "-p", "no:json-report", "-p", "collect_plugin",
        "--result-jsonl", tmp_report,
        *extra_args,
    ]
    output = ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=PER_FILE_CWD)
        output = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as exc:
        output = decode(exc.stdout) + decode(exc.stderr)
    except Exception as e:
        print(f"run_pytest: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return [], [], f"{type(e).__name__}: {e}"
    
    imports = [l.strip() for l in output.splitlines() if l.strip().startswith(IMPORT_FAILED_PREFIX)]
    
    tests: list[dict] = []
    if tmp_report.exists():
        try:
            for line in tmp_report.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    tests.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except IOError:
            pass
        tmp_report.unlink(missing_ok=True)
        
    return tests, imports, output.strip()[-2000:]


def run_single(path: str, extra_args: list[str]) -> tuple[list[dict], list[str], dict | None]:
    tests, imports, output_tail = run_pytest([path], RETRY_TIMEOUT, extra_args)
    if tests:
        return tests, imports, None
    msg = f"no test entries; pytest output tail: {output_tail!r}" if output_tail else "no test entries and no pytest output"
    return [], imports, error_entry(path, msg)


def main() -> None:
    output_path = sys.argv[1]
    extra_args = sys.argv[2:]
    files = [l.strip() for l in Path(FILE_LIST).read_text().splitlines() if l.strip()]
    seen: dict[str, dict] = {}
    imports: list[str] = []
    # Phase 1: one batched pytest run over all files (fast path).
    tests, new_imports, output_tail = run_pytest(files, EXEC_TIMEOUT, extra_args)
    
    for t in tests:
        seen.setdefault(t.get("nodeid", ""), t)
    
    imports.extend(new_imports)
    if not tests and output_tail:
        # Surface phase-1 crash info (goes to utils.py:collect_results as raw_output).
        print(f"FULL_RUN_TAIL: {output_tail!r}", file=sys.stderr)
        
    covered = {nid.split("::", 1)[0] for nid in seen}
    # Phase 2: any file with zero results gets retried in isolation so one broken file can't blank the rest.
    uncovered = [p for p in files if to_nodeid(p) not in covered and p not in covered]
    if uncovered:
        with ThreadPoolExecutor(max_workers=RETRY_WORKERS) as pool:
            futures = {pool.submit(run_single, p, extra_args): p for p in uncovered}
            for future in as_completed(futures):
                tests, imps, err = future.result()
                for t in tests:
                    seen.setdefault(t.get("nodeid", ""), t)
                imports.extend(imps)
                if err:
                    seen.setdefault(err["nodeid"], err)
    
    for line in imports:
        print(line, file=sys.stderr)
    with open(output_path, "w") as f:
        for entry in seen.values():
            f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()
