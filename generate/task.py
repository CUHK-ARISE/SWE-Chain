import json
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader

from .container import DockerContainer
from .create_report import create_report
from agent import AgentRunner
from parsers.diff_extractor import DiffExtractor
from validation import TestRunResult, run_cross_tests
from config import DOCKER_BASE_DIR, PACKAGES_ROOT, TMP_PATH, IMAGE_PREFIX, get_testing_folder, get_protected_dirs, get_protected_root_files, get_exec_timeout

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
ENV = Environment(loader=FileSystemLoader(PROMPTS_DIR))


ERROR_REPORT_PATH = "/app/error_report.md"


class ChainTask:
    def __init__(
        self,
        package: str,
        prev_version: str,
        next_version: str,
        agent: AgentRunner,
        max_iters: int,
        specs_str: str,
        container: DockerContainer,
        output_dir: Path,
        oracle_chain_dir: Path,
        live_log_path: str,
    ):
        self.package = package
        self.prev_version = prev_version
        self.next_version = next_version
        self.agent = agent
        self.max_iters = max_iters
        self.test_rel = get_testing_folder(package)
        self.extra_protected = [*get_protected_dirs(package), *get_protected_root_files(package)]
        self.specs_str = specs_str
        self.specs_filename = f"v{self.next_version}_specs.md"
        self.baseline_path = oracle_chain_dir / next_version / f"v{next_version}_test_results.json"
        self.baseline = TestRunResult.load(self.baseline_path)
        self.container = container
        self.output_dir = output_dir
        self.live_log_path = live_log_path
        self.round = 0
        self.success = True
        self.has_test_errors: bool = False
        self.last_pass_rate: float = 0.0
        self.error_msg: Optional[str] = None
        self.phases: List[Dict] = []
        self.trajectory: List[Dict] = []
        self.hidden_runs: List[Dict] = []
    
    @property
    def gold_prev_codebase(self) -> Path:
        return PACKAGES_ROOT / self.package / self.prev_version
    
    @property
    def gold_next_codebase(self) -> Path:
        return PACKAGES_ROOT / self.package / self.next_version
    
    def verify(self) -> None:
        if not self.specs_str:
            raise ValueError(f"No specs provided for {self.prev_version} -> {self.next_version}")
        if not self.baseline_path.exists():
            raise FileNotFoundError(f"Baseline not found: {self.baseline_path}")
    
    
    # ── Container setup ─────────────────────────────────────────────────
    def init_container(self) -> None:
        self.agent.setup_container(self.container)
        self.container.exec(f"rm -rf {DOCKER_BASE_DIR}")
        self.container.cp_to(str(self.gold_prev_codebase) + "/.", DOCKER_BASE_DIR)
        self.container.exec(f"rm -rf {DOCKER_BASE_DIR}/{self.test_rel}")
        self.container.exec(
            "useradd -m -s /bin/bash agent 2>/dev/null; "
            "chown -R agent:agent /home/agent /app 2>/dev/null; "
            "true",
            check=False,
        )
        self.container.exec("mkdir -p /released && chown root:root /released && chmod 700 /released")
        self.container.exec(f"cp -r {DOCKER_BASE_DIR} /released/{self.prev_version}")
        self.container.exec("chown -R root:root /released && chmod -R go-rwx /released")
    
    
    def prepare_step(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(self.specs_str)
            tmp_path = f.name
        try:
            self.container.cp_to(tmp_path, f"/app/{self.specs_filename}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        self.container.exec("chown -R agent:agent /app 2>/dev/null; true", check=False)
    
    
    # ── Helpers ──────────────────────────────────────────────────────────
    def get_prompt(self, template_name: str, **extra_vars) -> str:
        template = ENV.get_template(template_name)
        return template.render(
            package_name=self.package,
            old_version=self.prev_version,
            new_version=self.next_version,
            specs_filename=self.specs_filename,
            **extra_vars,
        )
    
    
    def release(self) -> None:
        self.container.exec(f"cp -r {DOCKER_BASE_DIR} /released/{self.next_version}")
        self.container.exec(
            f"mkdir -p /released/specs && "
            f"mv /app/{self.specs_filename} /released/specs/ 2>/dev/null; true"
        )
        self.container.exec(
            f"chown -R root:root /released/{self.next_version} /released/specs && "
            f"chmod -R go-rwx /released/{self.next_version} /released/specs"
        )
        print(f"  Released code and specs to /released/{self.next_version}")
    
    
    def write_error_report(self, content: str) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            tmp_path = f.name
        try:
            self.container.cp_to(tmp_path, ERROR_REPORT_PATH)
            self.container.exec(
                f"chown agent:agent {ERROR_REPORT_PATH} 2>/dev/null; chmod 644 {ERROR_REPORT_PATH} 2>/dev/null",
                check=False,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    
    
    def remove_error_report(self) -> None:
        self.container.exec(f"rm -f {ERROR_REPORT_PATH}", check=False)
    
    
    def extract_code(self, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        self.container.cp_from(f"{DOCKER_BASE_DIR}/.", str(dest))
    
    
    def record(self, result: Dict) -> None:
        self.trajectory.extend(result.get("trajectory", []))
        self.phases.append(result)
    
    
    def compute_diffs(self, snapshot_dir: Path):
        codebase_dir = TMP_PATH / f"modified_{secrets.token_hex(8)}"
        self.extract_code(codebase_dir)
        gold_diff_all = ""
        gold_diff_py = ""
        step_diff_all = ""
        step_diff_py = ""
        gold_dir = TMP_PATH / f"gold_{secrets.token_hex(8)}"
        try:
            shutil.copytree(str(self.gold_next_codebase), str(gold_dir))
            gold_extractor = DiffExtractor(str(gold_dir), str(codebase_dir))
            gold_diff_all, _ = gold_extractor.extract_diff(exclude_paths=[self.test_rel])
            gold_diff_py, _ = gold_extractor.extract_diff(match_exts=[".py"], exclude_paths=[self.test_rel])
            step_extractor = DiffExtractor(str(snapshot_dir), str(codebase_dir))
            step_diff_all, step_n_all = step_extractor.extract_diff(exclude_paths=[self.test_rel])
            step_diff_py, step_n_py = step_extractor.extract_diff(match_exts=[".py"], exclude_paths=[self.test_rel])
            print(f"  Step diff: {step_n_all} total, {step_n_py} Python (excluding tests)")
        except Exception as e:
            print(f"  ERROR: failed to compute diffs: {e}")
            self.success = False
            self.error_msg = f"Diff computation failed: {e}"
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            shutil.rmtree(codebase_dir, ignore_errors=True)
            shutil.rmtree(gold_dir, ignore_errors=True)
        return gold_diff_all, gold_diff_py, step_diff_all, step_diff_py
    
    
    def hidden_check(self, phase_snapshot: Path, phase_name: str) -> Dict:
        current_dir = TMP_PATH / f"phase_{secrets.token_hex(8)}"
        diff_all = ""
        diff_py = ""
        error_content: Optional[str] = None
        self.has_test_errors = False
        self.remove_error_report()
        self.extract_code(current_dir)
        image = f"{IMAGE_PREFIX}:{self.package}-{self.next_version}"
        try:
            print(f"  Running hidden tests (image: {image})...")
            result = run_cross_tests(
                image=image,
                source=current_dir,
                tests_rel=self.test_rel,
                baseline=self.baseline,
                prefix="swe-check",
                retry_workers=3,
                exec_timeout=get_exec_timeout(self.package),
                extra_protected=self.extra_protected,
            )
            self.hidden_runs.append({"phase": phase_name, "run": result.to_dict()})
            total_hidden = len(self.baseline.results)
            self.last_pass_rate = len(result.passed) / total_hidden if total_hidden else 0.0
            print(f"  Hidden tests: passed={len(result.passed)}, failed={len(result.failed)}, errors={len(result.errors)}")
            
            error_content = create_report(result)
            if error_content:
                self.write_error_report(error_content)
                self.has_test_errors = True
                unique_errors = len({(tr.message or "").strip() for tr in result.errors if (tr.message or "").strip()})
                print(f"  Wrote error report ({unique_errors} unique error blocks)")
            
            extractor = DiffExtractor(str(phase_snapshot), str(current_dir))
            diff_all, n_all = extractor.extract_diff(exclude_paths=[self.test_rel])
            diff_py, n_py = extractor.extract_diff(match_exts=[".py"], exclude_paths=[self.test_rel])
            print(f"  Phase diff: {n_all} total, {n_py} Python (excluding tests)")
        
        except Exception as e:
            print(f"  Warning: hidden_check failed: {e}")
            self.success = False
            self.error_msg = f"Hidden check infra failure during {phase_name}: {e}"
        
        finally:
            shutil.rmtree(current_dir, ignore_errors=True)
            shutil.rmtree(phase_snapshot, ignore_errors=True)
        
        return {
            "phase_diff": {"diff_all": diff_all, "diff_py": diff_py},
            "error_report": error_content,
        }
    
    
    def build(self):
        print("\n  [Phase 1] Developer building the new version...")
        snapshot = TMP_PATH / f"phase_snap_{secrets.token_hex(8)}"
        self.extract_code(snapshot)
        prompt = self.get_prompt("developer_build.j2")
        result = self.agent.run(prompt, self.container, live_log_path=self.live_log_path)
        result["phase"] = "build"
        if not result["success"]:
            self.success = False
            self.error_msg = f"Build agent failed: {result['error']}"
            print(f"  {self.error_msg}")
            self.record(result)
            shutil.rmtree(snapshot, ignore_errors=True)
            return
        result.update(self.hidden_check(snapshot, "build"))
        self.record(result)
    
    
    def fix(self):
        print(f"\n  [Phase 2] Developer fixing (iteration {self.round}/{self.max_iters - 1})...")
        snapshot = TMP_PATH / f"phase_snap_{secrets.token_hex(8)}"
        self.extract_code(snapshot)
        prompt = self.get_prompt("developer_fix.j2")
        result = self.agent.run(prompt, self.container, live_log_path=self.live_log_path)
        result["phase"] = f"fix_{self.round}"
        if not result["success"]:
            self.success = False
            self.error_msg = f"Fix agent failed (round {self.round}): {result['error']}"
            print(f"  {self.error_msg}")
            self.record(result)
            shutil.rmtree(snapshot, ignore_errors=True)
            return
        result.update(self.hidden_check(snapshot, f"fix_{self.round}"))
        self.record(result)
    
    
    def reset_session(self) -> None:
        self.agent.session_id = None
    
    
    def finalize(self, snapshot_dir: Path) -> Dict:
        gold_diff_all, gold_diff_py, step_diff_all, step_diff_py = self.compute_diffs(snapshot_dir)
        if self.success:
            self.reset_session()
        for phase in self.phases:
            phase.pop("trajectory", None)
        total_elapsed = sum(p.get("elapsed_seconds", 0) for p in self.phases)
        total_cost = sum(p.get("cost", 0) or 0 for p in self.phases)
        if self.hidden_runs:
            live_results_path = self.output_dir / "live_results.jsonl"
            live_results_path.parent.mkdir(parents=True, exist_ok=True)
            with open(live_results_path, "a") as f:
                for entry in self.hidden_runs:
                    row = {
                        "prev_version": self.prev_version,
                        "next_version": self.next_version,
                        "phase": entry["phase"],
                        "source": "live",
                        "success": self.success,
                        "run": entry["run"],
                    }
                    f.write(json.dumps(row) + "\n")
        result = {
            "prev_version": self.prev_version,
            "next_version": self.next_version,
            "model": self.agent.model,
            "round": self.round,
            "elapsed_seconds": round(total_elapsed, 1),
            "success": self.success,
            "error": self.error_msg,
            "cost": total_cost if total_cost > 0 else None,
            "pass_rate": self.last_pass_rate,
            "phases": self.phases,
            "gold_diff": {"diff_all": gold_diff_all, "diff_py": gold_diff_py},
            "agent_step_diff": {"diff_all": step_diff_all, "diff_py": step_diff_py},
            "trajectory": self.trajectory,
        }
        print(f"  {self.prev_version} -> {self.next_version}: {total_elapsed:.1f}s — {'OK' if self.success else self.error_msg}")
        return result
    
    
    # ── Main execution ───────────────────────────────────────────────────
    def run(self) -> Dict:
        self.prepare_step()
        snapshot_dir = TMP_PATH / f"snapshot_{secrets.token_hex(8)}"
        self.extract_code(snapshot_dir)
        self.build()
        self.round = 0
        while (
            self.round < self.max_iters - 1
            and self.success
            and self.has_test_errors
        ):
            self.round += 1
            self.fix()
        if self.success:
            self.release()
        self.remove_error_report()
        return self.finalize(snapshot_dir)
