import json
import hashlib
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from jinja2 import Environment, FileSystemLoader

from parsers.diff_parser import DiffParser
from synthetic.codex_caller import CodexCaller
from synthetic.task_spec import TaskSpec
from config import WORKSPACE_DIR

MAX_RETRY = 3

WORKERS = 3
WORKSPACE_DIR.mkdir(exist_ok=True)
OUTPUT_FILENAME = "output.json"
AGENT_TEMPLATE = Environment(loader=FileSystemLoader("prompts")).get_template("synthesis_agent.j2")

CORRECTION_MESSAGE = """
The output.json is invalid or empty. Please read it, fix it, and write valid JSON to output.json.
"""


class ConquerTaskRunner:
    def __init__(
        self,
        old_version: str,
        new_version: str,
        release_note_path: str,
        code_diff_path: str,
        code_matching_path: str,
        test_diff_path: str,
        test_matching_path: str,
        output_path: str,
        model: str,
        workers: int = WORKERS,
        continuous: bool = False,
    ):
        self.old_version = old_version
        self.new_version = new_version
        self.workers = workers
        self.continuous = continuous
        
        self.release_note_path = Path(release_note_path)
        self.code_matching_path = Path(code_matching_path)
        self.test_matching_path = Path(test_matching_path)
        self.output_path = Path(output_path)
        
        self.code_diff = DiffParser(code_diff_path)
        self.test_diff = DiffParser(test_diff_path)
        self.caller = CodexCaller(model=model)
        
        self.lock = threading.Lock()
        self.results: Dict[str, Any] = {}
        self.tasks_to_process: List[str] = []
    
    
    def load_data(self) -> None:
        # Load release notes and matching results
        notes = json.loads(self.release_note_path.read_text())
        self.release_note = {str(entry["task_id"]): entry for entry in notes}
        self.code_match = json.loads(self.code_matching_path.read_text())
        self.test_match = json.loads(self.test_matching_path.read_text())
        
        # Load existing results if continuous mode is on
        if not self.continuous or not self.output_path.exists():
            return
        try:
            self.results = json.loads(self.output_path.read_text())
            if self.results:
                print(f"  Resuming: {len(self.results)} tasks already processed")
        except (json.JSONDecodeError, KeyError):
            raise ValueError(f"Output file {self.output_path} is not valid JSON. Cannot resume.")
    
    
    def build(self) -> None:
        for task_id in set(self.code_match) | set(self.test_match):
            if task_id in ("others", "doc"):
                continue
            if task_id not in self.release_note:
                raise ValueError(f"Task ID {task_id} not found in release notes")
            if task_id not in self.results:
                self.tasks_to_process.append(task_id)
    
    
    def prepare_task(self, task_id: str) -> Tuple[Path, str, str]:
        rn = self.release_note[task_id]
        rn_content = rn["content"] + "\n\n" + "\n\n".join(
            gh["content"] for gh in rn.get("github", [])
        )
        
        code_diff_content = ""
        for entry in self.code_match.get(task_id, []):
            code_diff_content += self.code_diff.get_diff_by_hunk_headers(
                entry["diff_header"], hunk_headers=entry["hunks"], keep_index=False
            ) + "\n"
        
        test_diff_content = ""
        for entry in self.test_match.get(task_id, []):
            test_diff_content += self.test_diff.get_diff_by_hunk_headers(
                entry["diff_header"], hunk_headers=entry["hunks"], keep_index=False
            ) + "\n"
        
        task_hash = hashlib.md5(f"{task_id}_{self.new_version}".encode()).hexdigest()[:8]
        workspace = (WORKSPACE_DIR / f"synthetic_{task_hash}").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        
        rn_name = "release_note.md"
        code_diff_name = f"code_{task_hash}.diff"
        test_diff_name = f"test_{task_hash}.diff"
        (workspace / rn_name).write_text(rn_content)
        (workspace / code_diff_name).write_text(code_diff_content)
        (workspace / test_diff_name).write_text(test_diff_content)
        (workspace / OUTPUT_FILENAME).write_text("{}")
        
        prompt = AGENT_TEMPLATE.render(
            task_id=task_id,
            old_version=self.old_version,
            new_version=self.new_version,
            release_note_file=rn_name,
            code_diff_file=code_diff_name,
            test_diff_file=test_diff_name,
        )
        return workspace, prompt, task_hash
    
    
    def process_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        def validate(data):
            return TaskSpec.from_dict(data).to_dict()
        workspace, prompt, session_key = self.prepare_task(task_id)
        try:
            for attempt in range(MAX_RETRY):
                if attempt == 0:
                    self.caller.run(prompt, workspace, session_key=session_key)
                else:
                    self.caller.run(CORRECTION_MESSAGE.strip(), workspace, session_key=session_key)
                
                try:
                    data = json.loads((workspace / OUTPUT_FILENAME).read_text())
                    if data:
                        return validate(data)
                except (json.JSONDecodeError, FileNotFoundError, ValueError):
                    pass
                print(f"  Invalid output for task {task_id} (attempt {attempt+1}/{MAX_RETRY})")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    
    
    def run(self) -> None:
        self.load_data()
        self.build()
        if not self.tasks_to_process:
            self.output_path.write_text(json.dumps(self.results, indent=2, ensure_ascii=False))
            return
        total = len(self.tasks_to_process)
        completed = 0
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self.process_task, tid): tid for tid in self.tasks_to_process}
            for future in as_completed(futures):
                task_id = futures[future]
                data = future.result()
                completed += 1
                with self.lock:
                    self.results[task_id] = data
                    self.output_path.write_text(json.dumps(self.results, indent=2, ensure_ascii=False))
                print(f"  [{completed}/{total}] Completed: task {task_id}")
        print(f"  Done! -> {self.output_path}")
