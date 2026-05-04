import json
import shutil
import secrets
import threading
import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from evaluation.task import EvalTask
from evaluation.utils import phase_sort_key, iter_phases, read_jsonl, write_jsonl
from parsers.apply_diff import apply_diff
from config import PACKAGES_ROOT, ORACLE_DIR, TMP_PATH, get_testing_folder


class ChainReplay:
    def __init__(self, chain_results_path: str, mode: str = "both", max_workers: int = 4, resume: bool = False):
        self.mode = mode
        self.input_path = Path(chain_results_path)
        self.replay_curr_results_path = self.input_path.parent / "replay_curr_results.jsonl"
        self.replay_prev_results_path = self.input_path.parent / "replay_prev_results.jsonl"
        self.max_workers = max_workers
        chain_data = json.loads(self.input_path.read_text())
        
        self.package = chain_data["package"]
        self.chain = chain_data["chain"]
        self.start_version = chain_data["start_version"]
        self.end_version = chain_data["end_version"]
        self.oracle_chain_dir = ORACLE_DIR / f"{self.package}_{self.start_version}_to_{self.end_version}"
        
        self.phase_codebases = {}
        self.phase_order = []
        self.replay_curr_rows = []
        self.replay_prev_rows = []
        self.write_lock = threading.Lock()
        
        if not resume:
            for path in (self.replay_curr_results_path, self.replay_prev_results_path):
                path.unlink(missing_ok=True)
    
    
    def build_codebases(self) -> None:
        start_path = PACKAGES_ROOT / self.package / self.start_version
        if not start_path.exists():
            raise FileNotFoundError(f"Base codebase not found: {start_path}")
        tests_rel = get_testing_folder(self.package)
        current_path = start_path
        first_snapshot = True
        
        try:
            for step in self.chain:
                ver = step["next_version"]
                for phase in iter_phases(step):
                    phase_name = phase.get("phase", "build")
                    diff = (phase.get("phase_diff") or {}).get("diff_all", "")
                    snapshot = TMP_PATH / f"eval_{ver}_{phase_name}_{secrets.token_hex(4)}"
                    shutil.copytree(str(current_path), str(snapshot))
                    if first_snapshot:
                        shutil.rmtree(snapshot / tests_rel, ignore_errors=True)
                        first_snapshot = False
                    if diff.strip():
                        apply_diff(str(snapshot), diff)
                    key = (ver, phase_name)
                    self.phase_codebases[key] = snapshot
                    self.phase_order.append((step, phase_name))
                    current_path = snapshot
        except Exception:
            for path in self.phase_codebases.values():
                shutil.rmtree(path, ignore_errors=True)
            raise
        
        counts = Counter(name for _, name in self.phase_order)
        summary = " and ".join(f"{v} [{k}]" for k, v in sorted(counts.items(), key=lambda x: phase_sort_key(x[0])))
        print(f"  Built {summary}")
    
    
    def final_phase_name(self, step: Dict) -> str:
        return iter_phases(step)[-1].get("phase", "build")
    
    
    def load_existing(self, path: Path, has_phase: bool) -> Tuple[List[Dict], Set[Tuple]]:
        rows = list(read_jsonl(path))
        done: Set[Tuple] = set()
        for row in rows:
            key = (row.get("prev_version"), row.get("next_version"))
            if has_phase:
                key += (row.get("phase"),)
            done.add(key)
        return rows, done
    
    
    def append_row(self, path: Path, row: Dict, rows_list: List[Dict]) -> None:
        with self.write_lock:
            with open(path, "a") as f:
                f.write(json.dumps(row) + "\n")
                f.flush()
            rows_list.append(row)
    
    
    def prev_snapshots(self) -> Dict[str, Optional[Path]]:
        snaps: Dict[str, Optional[Path]] = {}
        last_final: Optional[Path] = None
        for step in self.chain:
            ver = step["next_version"]
            snaps[ver] = last_final
            last_final = self.phase_codebases.get((ver, self.final_phase_name(step)))
        return snaps
    
    
    def load_resume(self, want_curr: bool, want_prev: bool) -> Tuple[Set[Tuple], Set[Tuple]]:
        curr_done: Set[Tuple] = set()
        prev_done: Set[Tuple] = set()
        if want_curr:
            self.replay_curr_rows, curr_done = self.load_existing(self.replay_curr_results_path, has_phase=True)
        if want_prev:
            self.replay_prev_rows, prev_done = self.load_existing(self.replay_prev_results_path, has_phase=False)
        skipped = len(curr_done) + len(prev_done)
        if skipped:
            print(f"  Resuming: skipping {skipped} already-completed task(s)")
        return curr_done, prev_done
    
    
    def make_task(self, step: Dict, curr_codebase_dir: Optional[Path], prev_codebase_dir: Optional[Path]) -> EvalTask:
        return EvalTask(
            self.package,
            prev_version=step["prev_version"],
            next_version=step["next_version"],
            curr_codebase_dir=curr_codebase_dir,
            prev_codebase_dir=prev_codebase_dir,
            oracle_chain_dir=self.oracle_chain_dir,
        )
    
    
    def submit_curr(self, executor: ThreadPoolExecutor, curr_done: Set[Tuple]) -> Dict:
        futures = {}
        for (step, phase_name) in self.phase_order:
            ver = step["next_version"]
            if (step["prev_version"], ver, phase_name) in curr_done:
                continue
            task = self.make_task(step, self.phase_codebases[(ver, phase_name)], None)
            futures[executor.submit(task.run, "curr")] = ("curr", step, phase_name, task)
        return futures
    
    
    def submit_prev(self, executor: ThreadPoolExecutor, prev_snaps: Dict[str, Optional[Path]], prev_done: Set[Tuple]) -> Dict:
        futures = {}
        for step in self.chain:
            ver = step["next_version"]
            prev_snap = prev_snaps.get(ver)
            if prev_snap is None or (step["prev_version"], ver) in prev_done:
                continue
            task = self.make_task(step, None, prev_snap)
            futures[executor.submit(task.run, "prev")] = ("prev", step, None, task)
        return futures
    
    
    @staticmethod
    def make_row(kind: str, step: Dict, phase_name: Optional[str], task: EvalTask) -> Optional[Dict]:
        if kind == "curr" and task.curr_ran:
            run = task.curr_results.to_dict()
        elif kind == "prev" and task.prev_ran:
            run = task.prev_results.to_dict()
        else:
            return None
        row = {
            "prev_version": step["prev_version"],
            "next_version": step["next_version"],
            "source": f"replay_{kind}",
            "success": step["success"],
            "run": run,
        }
        if kind == "curr":
            row["phase"] = phase_name
        return row
    
    
    def collect(self, futures: Dict, targets: Dict[str, Tuple[Path, List[dict]]]) -> None:
        pbar = tqdm(as_completed(futures), total=len(futures), desc="  Evaluating", unit="task")
        for future in pbar:
            kind, step, phase_name, task = futures[future]
            try:
                future.result()
                row = self.make_row(kind, step, phase_name, task)
                if row is not None:
                    path, rows_list = targets[kind]
                    self.append_row(path, row, rows_list)
            except Exception as e:
                label = f"{step['prev_version']} -> {step['next_version']}"
                if phase_name:
                    label += f" [{phase_name}]"
                pbar.write(f"  [ERROR] replaying {label} ({kind}): {e}")
    
    
    def evaluate(self) -> None:
        want_curr = self.mode in ("curr", "both")
        want_prev = self.mode in ("prev", "both")
        curr_done, prev_done = self.load_resume(want_curr, want_prev)
        targets: Dict[str, Tuple[Path, List[dict]]] = {}
        if want_curr:
            targets["curr"] = (self.replay_curr_results_path, self.replay_curr_rows)
        if want_prev:
            targets["prev"] = (self.replay_prev_results_path, self.replay_prev_rows)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: Dict = {}
            if want_curr:
                futures.update(self.submit_curr(executor, curr_done))
            if want_prev:
                futures.update(self.submit_prev(executor, self.prev_snapshots(), prev_done))
            self.collect(futures, targets)
    
    
    def save(self) -> None:
        ver_order = {s["next_version"]: i for i, s in enumerate(self.chain)}
        if self.mode in ("curr", "both"):
            self.replay_curr_rows.sort(key=lambda r: (ver_order.get(r["next_version"], 999), phase_sort_key(r["phase"])))
            write_jsonl(self.replay_curr_results_path, self.replay_curr_rows)
            print(f"Replay-curr results saved to {self.replay_curr_results_path}")
        if self.mode in ("prev", "both"):
            self.replay_prev_rows.sort(key=lambda r: ver_order.get(r["next_version"], 999))
            write_jsonl(self.replay_prev_results_path, self.replay_prev_rows)
            print(f"Replay-prev results saved to {self.replay_prev_results_path}")
    
    
    def run(self) -> None:
        print(f"\n{'#'*60}")
        print(f"  Chain Replay: {self.package} ({len(self.chain)} steps, mode={self.mode}, {self.max_workers} workers)")
        print(f"{'#'*60}")
        self.build_codebases()
        self.evaluate()
        for path in self.phase_codebases.values():
            shutil.rmtree(path, ignore_errors=True)
        self.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay chain results: rebuild codebases from diffs and run cross-tests.")
    parser.add_argument("--chain-results", required=True, help="Path to chain.json")
    parser.add_argument("--mode", choices=["prev", "curr", "both"], default="both", help="Which codebases to test (prev for scoring, curr for soundness, both for both)")
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel evaluations (default: 4)")
    parser.add_argument("--resume", action="store_true", help="Resume from existing replay_{curr,prev}_results.jsonl; without this flag, they are deleted first")
    args = parser.parse_args()
    ChainReplay(chain_results_path=args.chain_results, mode=args.mode, max_workers=args.max_workers, resume=args.resume).run()
