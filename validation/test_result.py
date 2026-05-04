from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field

from parsers.test_node import TestNodeID


class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAIL = "xfail"
    XPASS = "xpass"


@dataclass
class TestResult:
    node: TestNodeID
    status: TestStatus
    duration: float = 0.0
    message: str = ""
    
    @property
    def nodeid(self) -> str:
        # Full test identifier including params, e.g., 'tests/test_basic.py::test_foo[a-b]'
        return str(self.node)
    
    @property
    def base_id(self) -> str:
        # Test identifier without params, for cross-domain comparison with diff nodes
        return self.node.base_id
    
    @property
    def test_file(self) -> str:
        # Path to the test file containing this test
        return self.node.path


@dataclass
class TestRunResult:
    """Results from a complete test run."""
    source_version: str
    test_version: str
    test_source: str
    results: dict[str, TestResult] = field(default_factory=dict)
    exit_code: int = -1
    raw_output: str = ""
    failed_imports: list[str] = field(default_factory=list)
    collection_error: str | None = None
    
    @property
    def passed(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.PASSED]
    
    @property
    def failed(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.FAILED]
    
    @property
    def errors(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.ERROR]
    
    @property
    def skipped(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.SKIPPED]
    
    @property
    def xfailed(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.XFAIL]
    
    @property
    def xpassed(self) -> list[TestResult]:
        return [r for r in self.results.values() if r.status == TestStatus.XPASS]
    
    @property
    def files(self) -> list[str]:
        return sorted({tr.test_file for tr in self.results.values()})
    
    @property
    def error_files(self) -> dict[str, list[str]]:
        file_errors: dict[str, set[str]] = {}
        for tr in self.results.values():
            if tr.status != TestStatus.ERROR or not tr.message:
                continue
            file_errors.setdefault(tr.test_file, set()).add(tr.message.strip())
        return {f: sorted(msgs) for f, msgs in sorted(file_errors.items())}
    
    
    def to_dict(self) -> dict:
        return {
            "source_version": self.source_version,
            "test_version": self.test_version,
            "test_source": self.test_source,
            "exit_code": self.exit_code,
            "failed_imports": self.failed_imports,
            "collection_error": self.collection_error,
            "files": self.files,
            "error_files": self.error_files,
            "results": {
                nodeid: {
                    "status": tr.status.value,
                    "duration": tr.duration,
                    "message": tr.message,
                }
                for nodeid, tr in self.results.items()
            },
        }
    
    
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    
    @classmethod
    def from_dict(cls, data: dict) -> TestRunResult:
        result = cls(
            source_version=data["source_version"],
            test_version=data.get("test_version", ""),
            test_source=data["test_source"],
            exit_code=data.get("exit_code", -1),
            failed_imports=data.get("failed_imports", []),
            collection_error=data.get("collection_error"),
        )
        for nodeid, tr_data in data.get("results", {}).items():
            node = TestNodeID.parse(nodeid)
            result.results[str(node)] = TestResult(
                node=node,
                status=TestStatus(tr_data["status"]),
                duration=tr_data.get("duration", 0.0),
                message=tr_data.get("message", ""),
            )
        return result
    
    
    @classmethod
    def load(cls, path: str | Path) -> TestRunResult:
        with open(path) as f:
            return cls.from_dict(json.load(f))
    
    
    @staticmethod
    def parse_pytest_jsonl(jsonl_str: str) -> dict[str, TestResult]:
        status_map = {
            "passed": TestStatus.PASSED,
            "failed": TestStatus.FAILED,
            "error": TestStatus.ERROR,
            "skipped": TestStatus.SKIPPED,
            "xfailed": TestStatus.XFAIL,
            "xpassed": TestStatus.XPASS,
        }
        
        results: dict[str, TestResult] = {}
        for line in jsonl_str.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                test = json.loads(line)
            except json.JSONDecodeError:
                continue
            node = TestNodeID.parse(test.get("nodeid", ""))
            results[str(node)] = TestResult(
                node=node,
                status=status_map.get(test.get("outcome", "error"), TestStatus.ERROR),
                duration=test.get("duration", 0.0),
                message=test.get("longrepr", ""),
            )
        
        return results


