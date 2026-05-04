import re
from typing import List, Tuple
from pydantic import BaseModel, Field

from .node import NodeID

HUNK_HEADER_RE = re.compile(r'^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@)(.*)')

class DiffHunk(BaseModel):
    raw_header: str = Field(description="Full hunk header line (e.g., @@ -a,b +c,d @@ class Foo:)")
    header: str = Field(default="", description="Range part only (e.g., @@ -a,b +c,d @@)")
    context: str = Field(default="", description="Trailing context label (e.g., class Foo:)")
    lines: List[str] = Field(default_factory=list, description="Lines in the hunk (starting with +, -, or space)")
    nodes: List[NodeID] = Field(default_factory=list, description="AST nodes affected by this hunk")

    def model_post_init(self, __context):
        if not self.header and self.raw_header:
            match = HUNK_HEADER_RE.match(self.raw_header)
            if match:
                self.header = match.group(1)
                if not self.context and match.group(2).strip():
                    self.context = match.group(2).strip()
            else:
                self.header = self.raw_header
    
    @property
    def content(self) -> str:
        return self.raw_header + "\n" + "\n".join(self.lines)
        
        
    @property
    def line_range(self) -> Tuple[int, int, int, int]:
        """Parse header into ``(old_start, old_count, new_start, new_count)``."""
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', self.header)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            return old_start, old_count, new_start, new_count
        return 0, 0, 0, 0
    
    def __repr__(self) -> str:
        return f"DiffHunk(header='{self.header}', lines={len(self.lines)})"
