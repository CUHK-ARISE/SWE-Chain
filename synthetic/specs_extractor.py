import json
from pathlib import Path
from typing import Dict, List, Literal, Tuple

from parsers.diff_parser import DiffParser
from synthetic.task_spec import TaskSpec, TaskType

TASK_TYPE_MAP = {
    TaskType.FEATURE: "New Features",
    TaskType.FIX: "Bug Fixes",
    TaskType.DEPENDENCY: "Dependency Updates",
    TaskType.BREAKING: "Breaking Changes",
    TaskType.TYPING: "Typing Updates",
    TaskType.DOCUMENTATION: "Documentation",
    TaskType.PERFORMANCE: "Performance Improvements",
    TaskType.REFACTOR: "Refactors",
    TaskType.OTHER: "Others",
}
Granularity = Literal["conceptual", "grounded"]


class SpecsExtractor:
    def __init__(self, old_version: str, new_version: str, base_specs_path: str, raw_tasks_path: str, diff_path: str, extra_specs_path: str) -> None:
        self.old_version = old_version
        self.new_version = new_version
        self.base_specs_path = Path(base_specs_path)
        self.raw_tasks_path = Path(raw_tasks_path)
        self.diff_path = Path(diff_path)
        self.extra_specs_path = Path(extra_specs_path)
        self.tasks_code_hunks_path = self.base_specs_path.parent / f"v{self.new_version}_tasks_code_hunks.json"
        self.tasks: List[TaskSpec] = []
        self.renderable_tasks: List[TaskSpec] = []
        self.additional_tasks: List[TaskSpec] = []
        self.raw_task_map: Dict[str, Dict] = {}
        self.task_code_hunks: Dict[str, List[Dict]] = {}
        self.diff: DiffParser
        self.base_task_count = 0
        self.extra_task_count = 0
        
    
    
    def load(self) -> None:
        if not self.raw_tasks_path.exists():
            raise FileNotFoundError(f"Tasks file not found: {self.raw_tasks_path}")
        if not self.base_specs_path.exists():
            raise FileNotFoundError(f"Specs file not found: {self.base_specs_path}")
        if not self.extra_specs_path.exists():
            raise FileNotFoundError(f"Extra specs file not found: {self.extra_specs_path}")
        if not self.diff_path.exists():
            raise FileNotFoundError(f"Diff file not found: {self.diff_path}")
        if not self.tasks_code_hunks_path.exists():
            raise FileNotFoundError(f"Tasks code hunks file not found: {self.tasks_code_hunks_path}")
        
        # Load base specs and extra specs
        base_specs = json.loads(self.base_specs_path.read_text())
        self.tasks = [TaskSpec.from_dict(spec) for _, spec in self.sorted_spec_items(base_specs)]
        self.base_task_count = len(self.tasks)
        extra_specs = json.loads(self.extra_specs_path.read_text())
        self.tasks.extend(TaskSpec.from_dict(spec) for _, spec in self.sorted_spec_items(extra_specs))
        self.extra_task_count = len(extra_specs)
        
        # Load raw tasks for potential GitHub content
        raw_tasks = json.loads(self.raw_tasks_path.read_text())
        self.raw_task_map = {task["task_id"]: task for task in raw_tasks}
        self.task_code_hunks = json.loads(self.tasks_code_hunks_path.read_text())
        self.renderable_tasks = []
        self.additional_tasks = []
        for task in self.tasks:
            if self.task_code_hunks.get(task.task_id):
                self.renderable_tasks.append(task)
            else:
                self.additional_tasks.append(task)
                
        self.diff = DiffParser(self.diff_path)
    
    
    @staticmethod
    def sorted_spec_items(specs: Dict) -> List[Tuple[str, Dict]]:
        return sorted(specs.items(), key=lambda item: int(item[0].split("_")[-1]))
    
    
    def group_tasks(self) -> Dict[TaskType, List[TaskSpec]]:
        grouped: Dict[TaskType, List[TaskSpec]] = {task_type: [] for task_type in TASK_TYPE_MAP}
        for task in self.renderable_tasks:
            grouped.setdefault(task.type, []).append(task)
        return grouped
    
    
    def render_additional(self) -> str:
        if not self.additional_tasks:
            return ""
        lines = ["## Additional Changes"]
        lines.extend(f"- {task.synthesized_requirement.problem_statement}" for task in self.additional_tasks)
        return "\n".join(lines)
    
    
    def render_task(self, task_idx: int, task: TaskSpec, granularity: Granularity, include_criteria: bool, include_github: bool, include_behavioral: bool) -> str:
        req = task.synthesized_requirement
        section_lines = [
            f"### Task {task_idx} {task.title}",
            f"{req.problem_statement}",
            "",
        ]
        section_lines.extend([
            f"- **Expectation**: {getattr(req.expectation, granularity)}",
            "",
            f"- **Constraints**: {getattr(req.constraints, granularity)}",
            "",
        ])
        
        if include_criteria:
            section_lines.append("- **Acceptance Criteria:**")
            section_lines.extend(f"  - {item}" for item in req.acceptance_criteria)
            section_lines.append("")
        
        if include_behavioral:
            section_lines.append("- **Behavioral Description:**")
            section_lines.extend(f"  - {item}" for item in req.behavior)
            section_lines.append("")
        
        if include_github and task.task_id in self.raw_task_map:
            section_lines.extend(
                item["content"]
                for item in self.raw_task_map[task.task_id].get("github", [])
                if item["type"] == "issue"
            )
        
        return "\n".join(section_lines)
    
    
    def extract_file_changes(self) -> List[str]:
        def normalize(path: str) -> str:
            if path.startswith(("a/", "b/")):
                return path[2:]
            return path
        changes: List[str] = []
        for file in self.diff.files:
            if file.rename_from and file.rename_to:
                changes.append(f"- Rename from `{normalize(file.rename_from)}` to `{normalize(file.rename_to)}`")
                continue
            if file.is_new_file:
                changes.append(f"- Add file `{normalize(file.new_path)}`")
                continue
        return changes
    
    
    def render(self, granularity: Granularity, include_criteria: bool, include_github: bool, include_behavioral: bool) -> str:
        grouped = self.group_tasks()
        sections = [f"# Upgrade Specification from {self.old_version} to {self.new_version}"]
        task_idx = 1
        
        for task_type, title in TASK_TYPE_MAP.items():
            if not grouped[task_type]:
                continue
            group_sections = [f"## {title}"]
            for task in grouped[task_type]:
                group_sections.append(
                    self.render_task(
                        task_idx=task_idx,
                        task=task,
                        granularity=granularity,
                        include_criteria=include_criteria,
                        include_github=include_github,
                        include_behavioral=include_behavioral,
                    )
                )
                task_idx += 1
            sections.append("\n\n".join(group_sections))
        
        additional = self.render_additional()
        if additional:
            sections.append(additional)
        
        file_changes = self.extract_file_changes()
        if file_changes:
            sections.append("## File Changes\n" + "\n".join(file_changes))
        
        return "\n\n".join(sections)
    
    
    def run(self, output_path: str, granularity: Granularity, include_criteria: bool, include_github: bool, include_behavioral: bool) -> None:
        self.load()
        output = self.render(
            granularity=granularity,
            include_criteria=include_criteria,
            include_github=include_github,
            include_behavioral=include_behavioral,
        )
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output)
        if self.extra_task_count:
            count_label = f"{self.base_task_count} + {self.extra_task_count}"
        else:
            count_label = str(self.base_task_count)
        print(f"Done! -> {output_file} ({count_label} tasks)")
