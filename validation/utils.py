from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from packaging.version import Version

from .container import DockerContainer
from .test_result import TestResult, TestRunResult, TestStatus
from parsers.test_node import TestNodeID
from config import DOCKER_BASE_DIR, DOCKER_SCRIPTS_DIR, get_dockerfiles

PLUGIN_DIR = "/tmp/pytest-plugins"
REPORT_PATH = "/tmp/report.jsonl"

EXEC_TIMEOUT_EXIT_CODE = 124


def image_exists(tag: str) -> bool:
    r = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def get_dockerfile(package: str, version: Version) -> Tuple[Path, dict]:
    for v_min, v_max, dockerfile, build_args in get_dockerfiles(package):
        if v_min <= version <= v_max:
            return dockerfile, build_args
    raise ValueError(f"No Dockerfile found for {package} version {version}")


def collect_test_files(c: DockerContainer, test_dir: str) -> List[str]:
    r = c.exec(f"pytest --collect-only -q {test_dir} 2>/dev/null", check=False)
    files = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if "::" in line:
            line = line.split("::", 1)[0]
        if line.endswith(".py"):
            files.add(line)
    return sorted(files)


def check_collection_error(c: DockerContainer, test_dir: str) -> str | None:
    r = c.exec(f"pytest --collect-only -q {test_dir}", check=False)
    if r.returncode == 0:
        return None
    output = (r.stderr or r.stdout).strip()[-2000:]
    return output or f"pytest --collect-only failed with exit code {r.returncode}"


def overlay_source(c: DockerContainer, source: Path, tests_rel: str, extra_protected: Optional[List[str]] = None) -> None:
    def cp_if_exists(src: str, dst: str) -> str:
        return f'if [ -e {src} ]; then mkdir -p "$(dirname {dst})" && cp -a {src} {dst}; fi'
    
    backup = "/tmp/target_overlay_backup"
    parts = [p for p in tests_rel.split("/") if p]
    # Per-package protected paths are passed via extra_protected (dirs + root files from packages.yaml).
    protected = [tests_rel] + ["/".join(parts[:i]) + "/conftest.py" for i in range(1, len(parts))]
    if extra_protected:
        protected.extend(extra_protected)
        
    c.exec(f"rm -rf {backup} && mkdir -p {backup}", user="root")
    for rel in protected:
        c.exec(cp_if_exists(f"{DOCKER_BASE_DIR}/{rel}", f"{backup}/{rel}"), user="root")
    
    c.cp_to(str(source) + "/.", DOCKER_BASE_DIR)
    
    for rel in protected:
        dst = f"{DOCKER_BASE_DIR}/{rel}"
        c.exec(f"rm -rf {dst} && {cp_if_exists(f'{backup}/{rel}', dst)}", user="root")
    c.chown(DOCKER_BASE_DIR)


def deploy(c: DockerContainer, test_files: List[str]) -> None:
    c.exec(f"mkdir -p {PLUGIN_DIR}")
    c.cp_to(str(DOCKER_SCRIPTS_DIR / "collect_plugin.py"), f"{PLUGIN_DIR}/collect_plugin.py")
    c.cp_to(str(DOCKER_SCRIPTS_DIR / "safe_import_plugin.py"), f"{PLUGIN_DIR}/safe_import_plugin.py")
    c.cp_to(str(DOCKER_SCRIPTS_DIR / "file_runner.py"), "/tmp/file_runner.py")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as tmp:
        tmp.write("\n".join(test_files) + "\n")
        tmp.flush()
        c.cp_to(tmp.name, "/tmp/test_files.txt")
    c.exec(f"chmod -R a+rX {PLUGIN_DIR} /tmp/file_runner.py /tmp/test_files.txt", user="root")


def run_file_tests(
    c: DockerContainer,
    retry_workers: int,
    retry_timeout: int,
    extra_env: Optional[Dict[str, str]] = None,
    extra_pytest_args: Optional[List[str]] = None,
    exec_timeout: int = 1200
) -> Tuple[int, str]:
    env = {
        "PYTHONPATH": PLUGIN_DIR,
        "RETRY_WORKERS": str(max(1, retry_workers)),
        "RETRY_TIMEOUT": str(max(1, retry_timeout)),
        "EXEC_TIMEOUT": str(max(1, exec_timeout)),
        **(extra_env or {}),
    }
    env_flags = [flag for k, v in env.items() for flag in ("-e", f"{k}={v}")]
    cmd = ["docker", "exec", *env_flags, c.name,
           "python", "/tmp/file_runner.py", REPORT_PATH,
           *(extra_pytest_args or [])]
    try:
        r = subprocess.run(
            cmd, stdout=sys.stderr, stderr=subprocess.PIPE, text=True,
            timeout=exec_timeout + 60,  # buffer for cleanup after file_runner's own kill fires
        )
        return r.returncode, r.stderr or ""
    except subprocess.TimeoutExpired as exc:
        # Kill stragglers; partial JSONL stays — expand_collection_entries backfills the rest as TIMEOUT.
        raw = exc.stderr or b""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        stderr_tail = (raw or "").strip()[-500:]
        c.exec(
            "pkill -9 -f 'file_runner.py' 2>/dev/null; "
            "pkill -9 -f 'pytest' 2>/dev/null; true",
            user="root", check=False, timeout=30,
        )
        return EXEC_TIMEOUT_EXIT_CODE, f"Timeout after {exec_timeout}s. stderr tail: {stderr_tail}"


def collect_results(c: DockerContainer, result: TestRunResult, error_context: str = "") -> None:
    if not c.file_exists(REPORT_PATH):
        tail = (result.raw_output or "").strip()[-500:]
        print(f"  [ERROR] JSONL report not found at {REPORT_PATH} (exit_code={result.exit_code})")
        if tail:
            print(f"  [ERROR] pytest output tail: {tail!r}")
        msg = f"JSONL report not found at {REPORT_PATH}"
        if error_context:
            msg += f" {error_context}"
        raise RuntimeError(msg)
    
    # docker cp streams via tar — avoids the buffer ceiling `docker exec cat` hits on big reports.
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        c.cp_from(REPORT_PATH, tmp_path)
        jsonl = Path(tmp_path).read_text()
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    
    # Empty file is OK: outer timeout fired before any test finished; backfill happens later.
    result.results = TestRunResult.parse_pytest_jsonl(jsonl)
    
    prefix = "IMPORT_FAILED: "
    result.failed_imports = sorted(set(
        line.strip()[len(prefix):]
        for line in result.raw_output.splitlines()
        if line.strip().startswith(prefix)
    ))


def relax_pytest_config(c: DockerContainer) -> None:
    strip_patterns = ["--strict-markers", "--strict-config"]
    for cfg in ("tox.ini", "setup.cfg", "pytest.ini"):
        if not c.file_exists(f"{DOCKER_BASE_DIR}/{cfg}"):
            continue
        if cfg == "setup.cfg":
            c.exec(f"sed -i 's/^\\[pytest\\]$/[tool:pytest]/' {DOCKER_BASE_DIR}/{cfg}")
        for pat in strip_patterns:
            c.exec(f"sed -i 's/{pat}//g' {DOCKER_BASE_DIR}/{cfg}")
    if c.file_exists(f"{DOCKER_BASE_DIR}/pyproject.toml"):
        for pat in ["--mypy-only-local-stub", "--mypy-pyproject-toml-file=pyproject.toml"]:
            c.exec(f"sed -i '/{pat}/d' {DOCKER_BASE_DIR}/pyproject.toml")
        c.exec(f"sed -i 's/-p pytest_mypy_plugins//g' {DOCKER_BASE_DIR}/pyproject.toml")


def expand_collection_entries(results: TestRunResult, baseline: TestRunResult) -> None:
    baseline_ids = set(baseline.results)
    for nid, tr in list(results.results.items()):
        # Only expand collection-level errors that don't match any baseline entry
        if tr.status not in (TestStatus.ERROR, TestStatus.SKIPPED):
            continue
        if nid in baseline_ids:
            continue
        
        # Strip "::<file>" sentinel so we can prefix-match real baseline children.
        prefix = nid[:-len("::<file>")] if nid.endswith("::<file>") else nid
        children = [
            child for child in baseline_ids
            if child.startswith(prefix + "::") and child not in results.results
        ]
        del results.results[nid]
        
        if children:
            # Parent node (module/class) — expand into individual test entries.
            for child in children:
                results.results[child] = TestResult(
                    node=TestNodeID.parse(child),
                    status=tr.status,
                    duration=0.0,
                    message=tr.message,
                )
            continue
        
        # Leaf test with no baseline match; lowercase last segment rules out class/module nodes.
        last = nid.rsplit("::", 1)[-1]
        if "::" in nid and last and not last[0].isupper():
            results.results[nid] = TestResult(
                node=tr.node,
                status=TestStatus.SKIPPED,
                duration=0.0,
                message=tr.message,
            )
    
    # Backfill baseline nodes still missing from results (parametrize gap or full-run timeout).
    raw = (results.raw_output or "").lstrip()
    if raw.startswith("Timeout after"):
        fallback_status = TestStatus.ERROR
        fallback_message = raw.split(".", 1)[0]
    else:
        fallback_status = TestStatus.FAILED
        fallback_message = "not collected by cross-version source"
    for nid in baseline.results:
        if nid not in results.results:
            results.results[nid] = TestResult(
                node=TestNodeID.parse(nid),
                status=fallback_status,
                duration=0.0,
                message=fallback_message,
            )
