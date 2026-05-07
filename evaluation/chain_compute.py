import json
import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple

from validation.test_result import TestRunResult, TestStatus
from evaluation.comparator import VersionComparator
from evaluation.utils import iter_phases, phase_sort_key, read_jsonl
from config import ORACLE_DIR


def aggregate(records: List[Dict]) -> Dict:
    tp = sum(r["classification"]["tp"] for r in records)
    fn = sum(r["classification"]["fn"] for r in records)
    fp = sum(r["classification"]["fp"] for r in records)
    recall    = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    if recall is None or precision is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {"recall": recall, "precision": precision, "f1": f1}


def compute_overall(chain: List[Dict]) -> Dict:
    by_step: Dict[Tuple[str, str], List[Dict]] = {}
    for r in chain:
        by_step.setdefault((r["prev_version"], r["next_version"]), []).append(r)
        
    build_records: List[Dict] = []
    build_fix_records: List[Dict] = []
    for records in by_step.values():
        by_phase = {r.get("phase"): r for r in records}
        if "build" in by_phase:
            build_records.append(by_phase["build"])
        fix_phases = sorted(
            (p for p in by_phase if isinstance(p, str) and p.startswith("fix_")),
            key=phase_sort_key,
        )
        if fix_phases:
            build_fix_records.append(by_phase[fix_phases[-1]])
        elif "build" in by_phase:
            build_fix_records.append(by_phase["build"])
            
    return {
        "build":     aggregate(build_records),
        "build+fix": aggregate(build_fix_records),
    }


def load_curr_jsonl(path: Path) -> Dict[Tuple[str, str, str], TestRunResult]:
    return {
        (row["prev_version"], row["next_version"], row["phase"]): TestRunResult.from_dict(row["run"])
        for row in read_jsonl(path)
    }


def load_prev_jsonl(path: Path) -> Dict[Tuple[str, str], TestRunResult]:
    return {
        (row["prev_version"], row["next_version"]): TestRunResult.from_dict(row["run"])
        for row in read_jsonl(path)
    }


class ChainCompute:
    def __init__(self, chain_results_path: str, curr_results_path: str, prev_results_path: str):
        # Paths
        self.chain_path = Path(chain_results_path)
        self.curr_path = Path(curr_results_path)
        self.prev_path = Path(prev_results_path)
        self.eval_path = self.chain_path.parent / "eval.json"
        chain_data = json.loads(self.chain_path.read_text())
        
        # metadata from chain.json
        self.package = chain_data["package"]
        self.chain = chain_data["chain"]
        self.model = chain_data["model"]
        self.provider = chain_data["provider"]
        self.agent = chain_data["agent"]
        self.start_version = chain_data["start_version"]
        self.end_version = chain_data["end_version"]
        
        self.oracle_chain_dir = ORACLE_DIR / f"{self.package}_{self.start_version}_to_{self.end_version}"
        self.curr_by_phase = load_curr_jsonl(self.curr_path)
        self.prev_by_step = load_prev_jsonl(self.prev_path)
        
        # Cache per-(prev_version, next_version): baseline, cross_baseline, upgrade_related
        self.step_cache = {}
        self.eval_results = []
    
    
    def load_step(self, prev_ver: str, next_ver: str) -> Dict:
        key = (prev_ver, next_ver)
        if key in self.step_cache:
            return self.step_cache[key]
        ver_dir = self.oracle_chain_dir / next_ver
        baseline = TestRunResult.load(ver_dir / f"v{next_ver}_test_results.json")
        cross_baseline = TestRunResult.load(ver_dir / f"v{prev_ver}_cross_test_results.json")
        comparator = VersionComparator(baseline, cross_baseline)
        entry = {
            "baseline": baseline,
            "cross_baseline": cross_baseline,
            "upgrade_related": comparator.f2p | comparator.e2p,
        }
        self.step_cache[key] = entry
        return entry
    
    
    @staticmethod
    def analyze(prev_version: str, next_version: str, baseline: TestRunResult, curr: TestRunResult, prev: TestRunResult, upgrade_related_nodes: Set) -> Dict:
        resolved, unresolved = set(), set()
        preserved, regressed = set(), set()
        recovered, unrecovered = set(), set()
        skipped = set()

        for nodeid, bl_node in baseline.results.items():
            if bl_node.status != TestStatus.PASSED:
                continue
            if nodeid not in curr.results or nodeid not in prev.results:
                skipped.add(nodeid)
                continue

            c = curr.results[nodeid]
            p = prev.results[nodeid]
            c_pass = c.status == TestStatus.PASSED
            c_fail = c.status in (TestStatus.FAILED, TestStatus.ERROR)
            p_pass = p.status == TestStatus.PASSED
            p_fail = p.status in (TestStatus.FAILED, TestStatus.ERROR)

            if not (c_pass or c_fail) or not (p_pass or p_fail):
                skipped.add(nodeid)
                continue

            upgrade_related = nodeid in upgrade_related_nodes
            if c_pass:
                if upgrade_related:
                    resolved.add(nodeid)         # TP: (·, pass), upgrade-related
                elif p_fail:
                    recovered.add(nodeid)        # (fail, pass), non-upgrade
                else:
                    preserved.add(nodeid)        # TN: (pass, pass), non-upgrade
            else:
                if upgrade_related:
                    unresolved.add(nodeid)       # FN: (·, fail), upgrade-related
                elif p_pass:
                    regressed.add(nodeid)        # FP: (pass, fail), non-upgrade
                else:
                    unrecovered.add(nodeid)      # (fail, fail), non-upgrade

        evaluated_nodes = resolved | unresolved | preserved | regressed | recovered | unrecovered
        status_counts = Counter(curr.results[n].status for n in evaluated_nodes)

        tp, fn = len(resolved), len(unresolved)
        fp, tn = len(regressed), len(preserved)


        def rate(num, den):
            return round(num / den, 4) if den else None


        return {
            "prev_version": prev_version,
            "next_version": next_version,
            "phase": None,
            "success": None,
            "evaluated": len(evaluated_nodes),
            "curr_results": {
                "passed": status_counts[TestStatus.PASSED],
                "failed": status_counts[TestStatus.FAILED],
                "error": status_counts[TestStatus.ERROR],
                "total": len(evaluated_nodes),
            },
            "classification": {
                "tp": tp, "fn": fn, "fp": fp, "tn": tn,
                "recovered": len(recovered), "unrecovered": len(unrecovered),
            },
            "metrics": {
                "resolving": rate(tp, tp + fn),
                "precision": rate(tp, tp + fp),
                "f1": rate(2 * tp, 2 * tp + fp + fn),
            },
        }


    @staticmethod
    def fmt_metric(label: str, value) -> str:
        return f"{label} N/A" if value is None else f"{label} {value:.1%}"
    
    
    def compute(self) -> None:
        for step in self.chain:
            prev_ver = step["prev_version"]
            next_ver = step["next_version"]
            step_data = self.load_step(prev_ver, next_ver)
            
            if prev_ver == self.start_version:
                prev = step_data["cross_baseline"]
            else:
                prev = self.prev_by_step[(prev_ver, next_ver)]
            
            for phase in iter_phases(step):
                phase_name = phase.get("phase", "build")
                curr = self.curr_by_phase.get((prev_ver, next_ver, phase_name))
                
                if curr is None:
                    print(f"  [skip] no curr row for {prev_ver} -> {next_ver} [{phase_name}]")
                    continue
                
                result = self.analyze(
                    prev_version=prev_ver,
                    next_version=next_ver,
                    baseline=step_data["baseline"],
                    curr=curr,
                    prev=prev,
                    upgrade_related_nodes=step_data["upgrade_related"],
                )
                result["phase"] = phase_name
                result["success"] = step.get("success", False)
                self.eval_results.append(result)
                
                m = result["metrics"]
                metrics = " | ".join([
                    self.fmt_metric("Res", m["resolving"]),
                    self.fmt_metric("Prec", m["precision"]),
                    self.fmt_metric("F1", m["f1"]),
                ])
                print(f"  [{prev_ver} -> {next_ver}] [{phase_name}] {metrics}")
    
    
    def save(self) -> None:
        meta = {"package": self.package, "model": self.model, "provider": self.provider, "agent": self.agent}
        overall = compute_overall(self.eval_results)
        with open(self.eval_path, "w") as f:
            json.dump({**meta, "overall": overall, "chain": self.eval_results}, f, indent=2)
        print(f"\nEval saved to {self.eval_path}")
    
    
    def run(self) -> None:
        print(f"\n{'#'*60}")
        print(f"  Chain Compute: {self.package} ({len(self.chain)} steps)")
        print(f"  curr: {self.curr_path}")
        print(f"  prev: {self.prev_path}")
        print(f"{'#'*60}")
        self.compute()
        self.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute eval.json from chain.json + curr/prev test run jsonls.")
    parser.add_argument("--chain-results", required=True, help="Path to chain.json")
    parser.add_argument("--curr-results", required=True, help="Path to curr results jsonl (live_results.jsonl or replay_curr_results.jsonl)")
    parser.add_argument("--prev-results", required=True, help="Path to prev results jsonl (replay_prev_results.jsonl)")
    args = parser.parse_args()
    ChainCompute(
        chain_results_path=args.chain_results,
        curr_results_path=args.curr_results,
        prev_results_path=args.prev_results,
    ).run()
