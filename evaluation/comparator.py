from __future__ import annotations

import warnings
from typing import Dict

from validation.test_result import TestRunResult, TestStatus


class VersionComparator:
    def __init__(self, baseline: TestRunResult, cross_baseline: TestRunResult) -> None:
        self.baseline = baseline
        self.cross_baseline = cross_baseline
        self.check_node_coverage()
        self.compute()
    
    
    def check_node_coverage(self) -> None:
        baseline_nodes = set(self.baseline.results.keys())
        cross_nodes = set(self.cross_baseline.results.keys())
        only_baseline = baseline_nodes - cross_nodes
        if only_baseline:
            warnings.warn(
                f"Node mismatch: {len(only_baseline)} baseline nodes "
                f"missing in cross_baseline ({len(baseline_nodes)} vs {len(cross_nodes)})"
            )
    
    
    def compute(self) -> None:
        self.f2p = set()
        self.e2p = set()
        self.p2p = set()
        self.p2f = set()
        self.f2f = set()
        common = self.baseline.results.keys() & self.cross_baseline.results.keys()
        for nodeid in common:
            b = self.baseline.results[nodeid].status
            c = self.cross_baseline.results[nodeid].status
            if b == TestStatus.PASSED and c == TestStatus.FAILED:
                self.f2p.add(nodeid)
            elif b == TestStatus.PASSED and c == TestStatus.ERROR:
                self.e2p.add(nodeid)
            elif b == TestStatus.PASSED and c == TestStatus.PASSED:
                self.p2p.add(nodeid)
            elif b in (TestStatus.FAILED, TestStatus.ERROR) and c == TestStatus.PASSED:
                self.p2f.add(nodeid)
            elif b in (TestStatus.FAILED, TestStatus.ERROR) and c in (TestStatus.FAILED, TestStatus.ERROR):
                self.f2f.add(nodeid)
        self.upgrade_related = self.f2p | self.e2p
    
    
    @property
    def summary(self) -> Dict:
        return {
            "pass_to_pass": len(self.p2p),
            "fail_to_pass": len(self.f2p),
            "error_to_pass": len(self.e2p),
            "pass_to_fail": len(self.p2f),
            "fail_to_fail": len(self.f2f),
            "upgrade_related": len(self.upgrade_related),
        }
