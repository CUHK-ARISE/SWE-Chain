from __future__ import annotations

import re
from typing import Iterable, List, Optional
from validation import TestRunResult

DEDUP_PATTERNS = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0x<ADDR>"),    # memory addresses
    (re.compile(r"/tmp/[^\s'\"]+"), "/tmp/<TMP>"),  # pytest tmp_path dirs
    (re.compile(r"at line \d+"), "at line <N>"),    # line-number drift
)


def dedup(s: str) -> str:
    for pattern, repl in DEDUP_PATTERNS:
        s = pattern.sub(repl, s)
    return s


def unique_stripped(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        s = (x or "").strip()
        if not s:
            continue
        key = dedup(s)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def create_report(result: TestRunResult) -> Optional[str]:
    sections: List[str] = []
    
    col = (result.collection_error or "").strip()
    if col:
        sections.append(f"## Collection error\n\n```\n{col}\n```")
    
    imports = unique_stripped(result.failed_imports)
    if imports:
        sections.append("## Failed imports\n\n" + "\n".join(f"- {s}" for s in imports))
    
    errors = unique_stripped(tr.message for tr in result.errors)[:20]
    if errors:
        blocks = "\n\n".join(f"```\n{m}\n```" for m in errors)
        sections.append(f"## Test setup errors\n\n{blocks}")
    
    if not sections:
        return None
    return "# Hidden Test Interface Errors\n\n" + "\n\n".join(sections) + "\n"
