import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Union

from jinja2 import Environment, FileSystemLoader

from synthetic.codex_caller import CodexCaller
from parsers.diff_parser import DiffParser
from synthetic.task_spec import TaskSpec
from config import WORKSPACE_DIR

MAX_RETRY = 3

WORKSPACE_DIR.mkdir(exist_ok=True)
OUTPUT_FILENAME = "new_tasks.json"
AGENT_TEMPLATE = Environment(loader=FileSystemLoader("prompts")).get_template("completing_agent.j2")

CORRECTION_MESSAGE = """
The new_tasks.json file is invalid. Please read it, fix it, and write valid JSON to new_tasks.json.

Requirements:
- Output must be a JSON object.
- If there are no meaningful missing tasks, write {}.
- If there are new tasks, use IDs starting from "new_task_1".
"""


class CompletingTaskRunner:
    def __init__(
        self,
        old_version: str,
        new_version: str,
        code_diff_path: Union[str, Path],
        code_matching_path: Union[str, Path],
        test_diff_path: Optional[Union[str, Path]],
        test_matching_path: Optional[Union[str, Path]],
        synthesized_path: Union[str, Path],
        output_path: Union[str, Path],
        model: str,
        continuous: bool = False,
    ):
        self.old_version = old_version
        self.new_version = new_version
        self.continuous = continuous
        
        self.code_diff_path = Path(code_diff_path)
        self.code_matching_path = Path(code_matching_path)
        self.test_diff_path = Path(test_diff_path) if test_diff_path else None
        self.test_matching_path = Path(test_matching_path) if test_matching_path else None
        self.synthesized_path = Path(synthesized_path)
        self.output_path = Path(output_path)
        
        self.code_diff = DiffParser(self.code_diff_path)
        self.test_diff = DiffParser(self.test_diff_path) if self.test_diff_path else None
        
        self.caller = CodexCaller(model=model)
        
        self.synthesized_tasks: Dict[str, Any] = {}
        self.code_match: Dict[str, Any] = {}
        self.test_match: Dict[str, Any] = {}
        self.code_others_diff_content = ""
        self.test_others_diff_content = ""
    
    
    def load_data(self) -> None:
        self.synthesized_tasks = json.loads(self.synthesized_path.read_text())
        self.code_match = json.loads(self.code_matching_path.read_text())
        if self.test_matching_path and self.test_matching_path.exists():
            self.test_match = json.loads(self.test_matching_path.read_text())
        else:
            self.test_match = {}
    
    
    def build(self) -> None:
        self.code_others_diff_content = "\n".join(
            segment for segment in self.collect_others(self.code_diff, self.code_match) if segment
        ).strip()

        if self.test_diff is not None:
            self.test_others_diff_content = "\n".join(
                segment for segment in self.collect_others(self.test_diff, self.test_match) if segment
            ).strip()
        else:
            self.test_others_diff_content = ""
    
    
    @staticmethod
    def collect_others(diff: DiffParser, match_data: Dict[str, Any]) -> list[str]:
        segments = []
        for entry in match_data.get("others", []):
            segment = diff.get_diff_by_hunk_headers(
                entry["diff_header"],
                hunk_headers=entry.get("hunks", []),
                keep_index=False,
            )
            if segment:
                segments.append(segment)
        return segments
    
    
    def prepare(self) -> tuple[Path, str, str]:
        session_key = hashlib.md5(f"{self.old_version}_{self.new_version}".encode()).hexdigest()[:8]
        workspace = (WORKSPACE_DIR / f"completing_{session_key}").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        
        (workspace / "synthesized_tasks.json").write_text(json.dumps(self.synthesized_tasks, indent=2, ensure_ascii=False))
        (workspace / "code_others.diff").write_text(self.code_others_diff_content)
        (workspace / "test_others.diff").write_text(self.test_others_diff_content)
        (workspace / OUTPUT_FILENAME).write_text("{}")
        
        prompt = AGENT_TEMPLATE.render(
            old_version=self.old_version,
            new_version=self.new_version,
            code_diff_file="code_others.diff",
            test_diff_file="test_others.diff",
        )
        return workspace, prompt, session_key
    
    
    @staticmethod
    def validate(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("Output must be a JSON object")
        normalized = {}
        for key, value in data.items():
            if not isinstance(key, str) or not key.startswith("new_task_"):
                raise ValueError("All keys must start with new_task_")
            if not isinstance(value, dict):
                raise ValueError("Each task spec must be a JSON object")
            task_spec = TaskSpec.from_dict(value)
            normalized[task_spec.task_id] = task_spec.to_dict()
        return normalized
    
    
    def process(self) -> Dict[str, Any]:
        if not self.code_others_diff_content:
            return {}
        workspace, prompt, session_key = self.prepare()
        
        try:
            for attempt in range(MAX_RETRY):
                if attempt == 0:
                    self.caller.run(prompt, workspace, session_key=session_key)
                else:
                    self.caller.run(CORRECTION_MESSAGE.strip(), workspace, session_key=session_key)
                
                try:
                    data = json.loads((workspace / OUTPUT_FILENAME).read_text())
                    return self.validate(data)
                except (json.JSONDecodeError, FileNotFoundError, ValueError):
                    data = None
                
                print(
                    f"  Invalid output for completing {self.old_version} -> {self.new_version} "
                    f"(attempt {attempt + 1}/{MAX_RETRY})"
                )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Failed to generate valid completing output for {self.old_version} -> {self.new_version}")
    
    
    def run(self) -> None:
        if self.continuous and self.output_path.exists():
            try:
                existing = json.loads(self.output_path.read_text())
            except json.JSONDecodeError as exc:
                raise ValueError(f"Output file {self.output_path} is not valid JSON. Cannot resume.") from exc
            
            if isinstance(existing, dict):
                print(f"  Skipping completing: existing output found at {self.output_path.name}")
                return
        
        self.load_data()
        self.build()
        
        if not self.code_others_diff_content:
            self.output_path.write_text(json.dumps({}, indent=2, ensure_ascii=False))
            print("  Skipping completing: no unmatched code hunks in others")
            return
        
        results = self.process()
        self.output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"  Done! ({len(results)} new tasks) -> {self.output_path}")
        
        