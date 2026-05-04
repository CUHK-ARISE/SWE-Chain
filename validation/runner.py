from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .container import DockerContainer
from .utils import (
    relax_pytest_config,
    check_collection_error,
    collect_results,
    collect_test_files,
    deploy,
    expand_collection_entries,
    overlay_source,
    run_file_tests,
)
from .test_result import TestRunResult
from config import DOCKER_BASE_DIR


def run_original_tests(
    image: str,
    source: Path,
    tests_rel: str,
    retry_workers: int = 1,
    retry_timeout: int = 300,
    exec_timeout: int = 1200,
) -> TestRunResult:
    source = Path(source).resolve()
    result = TestRunResult(
        source_version=source.name,
        test_version=source.name,
        test_source=f"{DOCKER_BASE_DIR}/{tests_rel}",
    )
    with DockerContainer.start(image) as c:
        test_files = collect_test_files(c, f"{DOCKER_BASE_DIR}/{tests_rel}")
        if not test_files:
            raise RuntimeError(f"No test files found under {DOCKER_BASE_DIR}/{tests_rel}")
        deploy(c, test_files)
        result.exit_code, result.raw_output = run_file_tests(
            c, retry_workers, retry_timeout, exec_timeout=exec_timeout,
        )
        collect_results(c, result)
    return result


def run_cross_tests(
    image: str,
    source: Path,
    tests_rel: str,
    baseline: TestRunResult,
    prefix: str,
    retry_workers: int = 1,
    retry_timeout: int = 300,
    exec_timeout: int = 1200,
    extra_protected: Optional[List[str]] = None,
) -> TestRunResult:
    source = Path(source).resolve()
    result = TestRunResult(
        source_version=source.name,
        test_version=baseline.source_version,
        test_source=f"{DOCKER_BASE_DIR}/{tests_rel}",
    )

    with DockerContainer.start(image, prefix=prefix) as c:
        # Overlay the codebase and restore protected files and test source
        overlay_source(c, source, tests_rel, extra_protected=extra_protected)
        
        # Reinstall the package without deps
        reinstall = c.exec(f"pip install -e {DOCKER_BASE_DIR} --no-deps -q", check=False)
        if reinstall.returncode != 0:
            print(f"  [WARN] pip install -e . --no-deps failed: {reinstall.stderr}")
        
        # Relax pytest config to avoid cross-version issues
        relax_pytest_config(c)
        
        # Check for collection errors
        result.collection_error = check_collection_error(c, f"{DOCKER_BASE_DIR}/{tests_rel}")
        
        # Deploy docker scripts and targeted test files
        targeted_files = [p.lstrip("/") for p in baseline.files]
        deploy(c, targeted_files)
        
        extra_env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        extra_pytest_args = [
            "-p", "safe_import_plugin",
            "-o", "filterwarnings=error",
            "-o", "filterwarnings=default::DeprecationWarning",
        ]
        result.exit_code, result.raw_output = run_file_tests(
            c, retry_workers, retry_timeout,
            extra_env=extra_env,
            extra_pytest_args=extra_pytest_args,
            exec_timeout=exec_timeout,
        )
        collect_results(
            c, result,
            error_context=f"(exit_code={result.exit_code}, output_tail={(result.raw_output or '').strip()[-2000:]!r})",
        )
    
    expand_collection_entries(result, baseline)
    
    return result
