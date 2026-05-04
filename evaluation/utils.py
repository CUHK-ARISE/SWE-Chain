import json
from pathlib import Path
from typing import Dict, Iterator, List


def phase_sort_key(phase_name: str) -> int:
    if phase_name == "build":
        return 0
    if phase_name.startswith("fix_"):
        try:
            return 1 + int(phase_name.split("_", 1)[1])
        except (ValueError, IndexError):
            return 999
    return 999


def iter_phases(step: Dict) -> List[Dict]:
    """Sorted phase dicts; synthesize a single 'build' phase carrying the step's whole diff if none."""
    phases = step.get("phases") or []
    if not phases:
        return [{"phase": "build", "phase_diff": step.get("agent_step_diff", {})}]
    return sorted(phases, key=lambda p: phase_sort_key(p.get("phase", "")))


def read_jsonl(path: Path) -> Iterator[Dict]:
    """Yield parsed rows from a jsonl file, skipping blank/malformed lines."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
