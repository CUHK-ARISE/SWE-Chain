import json
import hashlib
import shutil
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from typing import Dict, List, Tuple, Any, Set, Union, Optional

from parsers.diff_parser import DiffParser
from synthetic.codex_caller import CodexCaller
from config import WORKSPACE_DIR

DEFAULT_MAX_FILES = 5
DEFAULT_MAX_HUNKS = 50
DEFAULT_MAX_LINES = 800
MAX_RETRY = 5

WORKSPACE_DIR.mkdir(exist_ok=True)
OUTPUT_FILENAME = "output.json"
AGENT_TEMPLATE = Environment(loader=FileSystemLoader("prompts")).get_template("matching_agent.j2")

CORRECTION_MESSAGE = """
{message}
Please update {output_file} with corrected results.
"""

class HunkMatchingRunner:
    def __init__(
        self,
        diff_path: Union[str, Path],
        release_note_path: Union[str, Path],
        output_path: Union[str, Path],
        model: str,
        max_files: int = DEFAULT_MAX_FILES,
        max_hunks: int = DEFAULT_MAX_HUNKS,
        max_lines: int = DEFAULT_MAX_LINES,
        continuous: bool = False,
    ):
        self.diff_path = Path(diff_path)
        self.release_note_path = Path(release_note_path)
        self.output_path = Path(output_path)
        self.max_files = max_files
        self.max_hunks = max_hunks
        self.max_lines = max_lines
        self.continuous = continuous
        self.whole_diff = DiffParser(diff_path)
        self.caller = CodexCaller(model=model)
        
        # Internal state
        self.results: Dict[str, List[Any]] = {}
        self.header_to_info: Dict[str, Dict[str, str]] = {}
        self.tasks: List[Tuple[str, Dict[str, str]]] = []
        self.batches: List[List[Tuple[str, Dict[str, str]]]] = []
    
    
    def load_existing_headers(self) -> Set[str]:
        if not self.continuous:
            return set()
        try:
            self.results = json.loads(self.output_path.read_text())
            processed = {entry["diff_header"] for entries in self.results.values() for entry in entries}
            return processed
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            return set()
    
    
    def build_tasks(self) -> None:
        processed_headers = self.load_existing_headers()
        for f in self.whole_diff.files:
            if not f.hunks:
                continue
            if f.header not in processed_headers:
                self.tasks.append((f.content, {
                    "diff_header": f.header,
                    "old_path": f.old_path,
                    "new_path": f.new_path,
                    "filepath": f.filepath,
                }))
    
    
    def build_batches(self) -> None:
        metrics = [
            (len(d.strip().split('\n')), d.count('\n@@'), d, info)
            for d, info in self.tasks
        ]
        metrics.sort(key=lambda x: x[0], reverse=True)
        stats: List[Dict[str, int]] = []
        for lines, hunks, diff, info in metrics:
            placed = False
            for i, s in enumerate(stats):
                if (s["files"] + 1 <= self.max_files and
                    s["hunks"] + hunks <= self.max_hunks and
                    s["lines"] + lines <= self.max_lines):
                    self.batches[i].append((diff, info))
                    s["files"] += 1
                    s["hunks"] += hunks
                    s["lines"] += lines
                    placed = True
                    break
            if not placed:
                self.batches.append([(diff, info)])
                stats.append({"files": 1, "hunks": hunks, "lines": lines})
        print(f"  Processing {len(self.tasks)} files in {len(self.batches)} batches")
    
    
    def prepare_batch(self, batch: List[Tuple[str, Dict[str, str]]]) -> Tuple[Path, str, str]:
        """Create a workspace for this batch, returns (workspace, prompt, session_key)."""
        combined_diff = "\n".join(d for d, _ in batch)
        diff_hash = hashlib.md5(combined_diff.encode()).hexdigest()[:8]
        
        # Create a unique workspace for this batch
        workspace = (WORKSPACE_DIR / f"matching_{diff_hash}").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Copy the release note and diff to the workspace
        shutil.copy2(self.release_note_path, workspace / self.release_note_path.name)
        diff_name = f"code_{diff_hash}.diff"
        (workspace / diff_name).write_text(combined_diff)
        tasks = json.loads(self.release_note_path.read_text())
        skeleton = {t["task_id"]: [] for t in tasks} | {"doc": [], "others": []}
        (workspace / OUTPUT_FILENAME).write_text(json.dumps(skeleton, indent=2, ensure_ascii=False))
        
        # Use diff hash as session key to enable continuous mode to recognize batches
        prompt = AGENT_TEMPLATE.render(
            release_note_file=self.release_note_path.name,
            code_diff_file=diff_name,
        )
        return workspace, prompt, diff_hash
    
    
    def process_batch(self, batch: List[Tuple[str, Dict[str, str]]]) -> Optional[Dict[str, Any]]:
        batch_header_to_info = {info["diff_header"]: info for _, info in batch}
        workspace, prompt, session_key = self.prepare_batch(batch)
        
        try:
            for attempt in range(MAX_RETRY):
                if attempt == 0:
                    self.caller.run(prompt, workspace, session_key=session_key)
                else:
                    self.caller.run(correction_prompt, workspace, session_key=session_key)
                
                try:
                    data = json.loads((workspace / OUTPUT_FILENAME).read_text())
                except (json.JSONDecodeError, FileNotFoundError) as e:
                    print(f"  Error reading output: {e} (attempt {attempt+1}/{MAX_RETRY})")
                    continue
                
                unmatched, missing = self.validate(data, batch_header_to_info)
                
                if not unmatched and not missing:
                    return data
                
                if attempt < MAX_RETRY - 1:
                    message = f"{unmatched} unmatched, {missing} missing"
                    print(f"  {message} (attempt {attempt+1}/{MAX_RETRY})")
                    correction_prompt = CORRECTION_MESSAGE.format(
                        message=message, output_file=OUTPUT_FILENAME,
                    ).strip()
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    
    
    def validate(self, data: Dict[str, Any], batch_header_to_info: Dict[str, Dict[str, str]]) -> Tuple[int, int]:
        mentioned = set()
        unmatch_hunks = []
        # Check if all hunks in the response exist in the original diff
        for entries in data.values():
            for entry in entries:
                diff_header = entry.get("header")
                hunks = entry.get("hunks", [])
                if diff_header not in batch_header_to_info:
                    unmatch_hunks.extend(hunks)
                    continue
                for hunk in hunks:
                    if not self.whole_diff.check_hunks_exist(diff_header, [hunk]):
                        unmatch_hunks.append(hunk)
                    else:
                        mentioned.add((diff_header, hunk))
        
        # Check if there are hunks in the original diff that are not mentioned in the response
        missing_hunks = [
            hunk.header for f in self.whole_diff.files if f.header in batch_header_to_info
            for hunk in f.hunks if (f.header, hunk.header) not in mentioned
        ]
        return len(unmatch_hunks), len(missing_hunks)
    
    
    def run(self) -> None:
        self.build_tasks()
        self.build_batches()
        self.header_to_info = {info["diff_header"]: info for _, info in self.tasks}
        for i, batch in enumerate(self.batches):
            label = self.batch_label(batch)
            data = self.process_batch(batch)
            if data is None:
                raise RuntimeError(f"Failed to get valid response for batch [{label}]")
            self.store_results(data)
            print(f"  [{i+1}/{len(self.batches)}] Completed: {label}")
        self.output_path.write_text(json.dumps(self.results, indent=2, ensure_ascii=False))
        print(f"  Done! -> {self.output_path}")
    
    
    def store_results(self, data: Dict[str, Any]) -> None:
        for task_id, entries in data.items():
            if task_id not in self.results:
                self.results[task_id] = []
            for entry in entries:
                diff_header = entry["header"]
                hunk_strs = entry.get("hunks", [])
                if not hunk_strs:
                    continue
                info = self.header_to_info[diff_header]
                self.merge_hunks(self.results[task_id], diff_header, info["old_path"], info["new_path"], hunk_strs)
    
    
    @staticmethod
    def merge_hunks(result_entries, diff_header, old_path, new_path, hunk_strs):
        for entry in result_entries:
            if entry["diff_header"] == diff_header and entry["old_path"] == old_path and entry["new_path"] == new_path:
                for h in hunk_strs:
                    if h not in entry["hunks"]:
                        entry["hunks"].append(h)
                return
        result_entries.append({"diff_header": diff_header, "old_path": old_path, "new_path": new_path, "hunks": list(hunk_strs)})
    
    @staticmethod
    def batch_label(batch):
        files = [info["filepath"] for _, info in batch]
        return ", ".join(files) if len(files) <= 3 else f"{files[0]} + {len(files)-1} more"
