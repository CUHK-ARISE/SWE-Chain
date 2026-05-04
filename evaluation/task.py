from pathlib import Path
from typing import Optional

from validation.runner import run_cross_tests
from validation.test_result import TestRunResult
from config import IMAGE_PREFIX, get_testing_folder, get_protected_dirs, get_protected_root_files, get_exec_timeout


class EvalTask:
    def __init__(self, package: str, prev_version: str, next_version: str, curr_codebase_dir: Optional[Path], prev_codebase_dir: Optional[Path], oracle_chain_dir: Path) -> None:
        self.package = package
        self.prev_version = prev_version
        self.next_version = next_version
        self.curr_codebase_dir = curr_codebase_dir
        self.prev_codebase_dir = prev_codebase_dir
        self.tests_rel = get_testing_folder(package)
        self.extra_protected = [*get_protected_dirs(package), *get_protected_root_files(package)]
        self.docker_image = f"{IMAGE_PREFIX}:{package}-{self.next_version}"
        ver_dir = oracle_chain_dir / self.next_version
        self.baseline_path = ver_dir / f"v{self.next_version}_test_results.json"
        self.baseline = TestRunResult.load(self.baseline_path)
        self.retry_workers = 3
        self.exec_timeout = get_exec_timeout(package)
        self.curr_results = None
        self.prev_results = None
        self.curr_ran = False
        self.prev_ran = False
    
    
    def run_tests(self, source: Path) -> TestRunResult:
        return run_cross_tests(
            image=self.docker_image,
            source=source,
            tests_rel=self.tests_rel,
            baseline=self.baseline,
            prefix="swe-eval",
            retry_workers=self.retry_workers,
            exec_timeout=self.exec_timeout,
            extra_protected=self.extra_protected,
        )
    
    
    def run(self, mode: str) -> None:
        if mode not in ("prev", "curr", "both"):
            raise ValueError(f"Unknown mode: {mode}")
        if mode in ("curr", "both") and self.curr_codebase_dir is not None:
            self.curr_results = self.run_tests(self.curr_codebase_dir)
            self.curr_ran = True
        if mode in ("prev", "both") and self.prev_codebase_dir is not None:
            self.prev_results = self.run_tests(self.prev_codebase_dir)
            self.prev_ran = True
