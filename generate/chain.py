import json
import re
import argparse
import secrets
import shutil
from pathlib import Path
from typing import List, Dict, Optional


from agent.opencode import OpenCodeRunner
from agent.claudecode import ClaudeCodeRunner
from agent.codex import CodexRunner
from generate.container import DockerContainer
from generate.setup import prepare_chain
from generate.task import ChainTask
from parsers.apply_diff import apply_diff
from config import DOCKER_BASE_DIR, ORACLE_DIR, TMP_PATH, IMAGE_PREFIX

AGENT_CLASSES = {
    "opencode": OpenCodeRunner,
    "claudecode": ClaudeCodeRunner,
    "codex": CodexRunner,
}

class ChainRunner:
    def __init__(
        self,
        data_file: str,
        model: str,
        provider: str,
        agent: str,
        each_timeout: int = 7200,
        effort: Optional[str] = None,
        max_iters: int = 2,
        container_id: Optional[str] = None,
    ) -> None:
        self.data_file = Path(data_file)
        self.rows = self.load_data()
        self.package = self.rows[0]["package"]
        self.start = self.rows[0]["prev_ver"]
        self.end = self.rows[-1]["next_ver"]
        self.chain_name = f"{self.package}_{self.start}_to_{self.end}"
        self.oracle_chain_dir = ORACLE_DIR / self.chain_name
        self.model = model
        self.provider = provider
        self.agent_cli = agent
        self.max_iters = max_iters
        self.container_id = container_id
        
        # Initialize agents
        runner_cls = AGENT_CLASSES[agent]
        agent_model = f"{provider}/{model}" if agent == "opencode" else model
        self.agent = runner_cls(model=agent_model, timeout=each_timeout, effort=effort)
        
        # Docker container
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "-", model).strip(".-") or "model"
        if container_id:
            self.container = DockerContainer.attach(container_id)
        else:
            self.prepare_environment()
            image = f"{IMAGE_PREFIX}:{self.package}-{self.start}"
            container_name = f"{IMAGE_PREFIX}-{self.package}-{agent}-{safe_model}-{secrets.token_hex(4)}"
            self.container = DockerContainer.start(image, container_name)
        
        # Output directories
        self.output_dir = Path(f"results/{self.chain_name}/{agent}-{provider}-{safe_model}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / "chain.json"
        self.record_path = self.output_dir / "record.jsonl"
        self.live_log_path = self.output_dir / "live_log.jsonl"
        
        # State
        self.tasks: List[ChainTask] = []
        self.chain: List[Dict] = []
    
    
    def prepare_environment(self) -> None:
        versions = [self.rows[0]["prev_ver"]] + [r["next_ver"] for r in self.rows]
        prepare_chain(self.package, versions)
    
    
    def load_data(self) -> List[Dict]:
        with open(self.data_file) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if not rows:
            raise ValueError(f"No data found in {self.data_file}")
        return rows
    
    
    def replay_diffs(self) -> None:
        print(f"  Replaying {len(self.chain)} step diffs to reconstruct container state...")
        local_dir = TMP_PATH / f"replay_{secrets.token_hex(8)}"
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.container.cp_from(f"{DOCKER_BASE_DIR}/.", str(local_dir))
            for step in self.chain:
                diff_all = step["agent_step_diff"]["diff_all"]
                if diff_all:
                    apply_diff(str(local_dir), diff_all)
            self.container.exec(f"rm -rf {DOCKER_BASE_DIR}", check=False)
            self.container.cp_to(str(local_dir) + "/.", DOCKER_BASE_DIR)
            self.container.exec(f"chown -R agent:agent {DOCKER_BASE_DIR} 2>/dev/null; true", check=False)
            for step in self.chain:
                self.container.exec(f"cp -r {DOCKER_BASE_DIR} /released/{step['next_version']}")
            self.container.exec("chown -R root:root /released && chmod -R go-rwx /released")
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)
    
    
    def build_tasks(self) -> None:
        for row in self.rows:
            prev_ver, next_ver = row["prev_ver"], row["next_ver"]
            task = ChainTask(
                self.package, prev_ver, next_ver,
                self.agent,
                self.max_iters,
                specs_str=row["specs"],
                container=self.container,
                output_dir=self.output_dir,
                oracle_chain_dir=self.oracle_chain_dir,
                live_log_path=str(self.live_log_path),
            )
            task.verify()
            self.tasks.append(task)
        if not self.container_id:
            self.tasks[0].init_container()
    
    
    def load_resume(self) -> int:
        if not self.output_path.exists():
            return 0
        with open(self.output_path) as f:
            existing = json.load(f)
        self.chain = [s for s in existing["chain"] if s.get("success", False)]
        done = {(s["prev_version"], s["next_version"]) for s in self.chain}
        for idx, task in enumerate(self.tasks):
            if (task.prev_version, task.next_version) not in done:
                return idx
        return len(self.tasks)
    
    
    def save_step(self, step_result: Dict) -> None:
        trajectory = step_result.pop("trajectory", [])
        if trajectory:
            entry = {
                "prev_version": step_result["prev_version"],
                "next_version": step_result["next_version"],
                "events": trajectory,
            }
            with open(self.record_path, "a") as rf:
                rf.write(json.dumps(entry) + "\n")
            print(f"  Trajectory ({len(trajectory)} events) saved to {self.record_path}")
        self.chain.append(step_result)
        output = {
            "package": self.package,
            "start_version": self.start,
            "end_version": self.end,
            "model": self.model,
            "provider": self.provider,
            "agent": self.agent_cli,
            "chain": self.chain,
        }
        with open(self.output_path, "w") as f:
            json.dump(output, f, indent=2)
    
    
    def print_summary(self) -> None:
        print(f"\n{'#'*60}")
        print(f"  Chain Complete: {self.start} -> {self.end}")
        print(f"{'#'*60}")
        for step in self.chain:
            status = "OK" if step["success"] else f"FAILED: {step['error']}"
            print(f"  {step['prev_version']} -> {step['next_version']}: {step['elapsed_seconds']}s — {status}")
        print(f"\nResults saved to {self.output_path}")
    
    
    def recover_container(self, resume_version: str) -> None:
        print(f"  Recovering container state from /released/{resume_version}...")
        self.container.exec(f"rm -rf {DOCKER_BASE_DIR} && cp -r /released/{resume_version} {DOCKER_BASE_DIR} && chown -R agent:agent {DOCKER_BASE_DIR} 2>/dev/null; chmod 700 /released 2>/dev/null; true", workdir="/", check=True)
    
    
    def run(self, resume: bool = False) -> None:
        self.build_tasks()
        
        skip_until = 0
        if resume or self.container_id:
            skip_until = self.load_resume()
        
        if skip_until >= len(self.tasks):
            print(f"\n  All {len(self.tasks)} tasks already completed; nothing to run.")
            self.print_summary()
            return
        
        if self.chain and self.container_id:
            self.recover_container(self.tasks[skip_until].prev_version)
        elif self.chain:
            self.replay_diffs()
        
        print(f"\n  {self.package} {self.start} -> {self.end} | {len(self.tasks)} tasks | {self.model} | max_iters={self.max_iters}")
        for i, task in enumerate(self.tasks[skip_until:], skip_until + 1):
            print(f"\n{'#'*60}")
            print(f"  [{i}/{len(self.tasks)}] {task.prev_version} -> {task.next_version}")
            print(f"{'#'*60}")
            step_result = task.run()
            self.save_step(step_result)
            if not step_result["success"]:
                break
        
        self.print_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run agent on a chain of version upgrades.")
    parser.add_argument("--data", required=True, help="Path to JSONL data file (e.g., data/flask_chain.jsonl)")
    parser.add_argument("--model", required=True, help="LLM model name")
    parser.add_argument("--provider", required=True, help="LLM provider (openai, anthropic, qwen)")
    parser.add_argument("--agent", required=True, choices=["opencode", "claudecode", "codex"], help="Agent CLI to use (opencode, claudecode, or codex)")
    parser.add_argument("--each-timeout", type=int, default=7200, help="Timeout per step in seconds")
    parser.add_argument("--effort", default=None, help="Model effort / reasoning effort (e.g., high, max, minimal)")
    parser.add_argument("--max-iters", type=int, default=2, help="Max review-fix iterations (default: 2)")
    parser.add_argument("--resume", action="store_true", help="Resume from existing results")
    parser.add_argument("--container-id", default=None, help="Attach to an existing container instead of starting a new one (implies --resume)")
    args = parser.parse_args()
    effort = args.effort if args.effort and args.effort.lower() != "none" else None
    runner = ChainRunner(
        data_file=args.data,
        model=args.model,
        provider=args.provider,
        agent=args.agent,
        each_timeout=args.each_timeout,
        effort=effort,
        max_iters=args.max_iters,
        container_id=args.container_id,
    )
    runner.run(resume=args.resume)
