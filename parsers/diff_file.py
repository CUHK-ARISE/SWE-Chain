from typing import List
from pydantic import BaseModel, Field, ConfigDict, computed_field

from .diff_hunk import DiffHunk

class DiffFile(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    header: str = Field(description="Diff header line (e.g., diff --git a/path/to/file b/path/to/file)")
    old_path: str = Field(description="Old file path (--- a/path/to/file)")
    new_path: str = Field(description="New file path (+++ b/path/to/file)")
    hunks: List[DiffHunk] = Field(default_factory=list, description="List of hunks in this file")
    header_lines: List[str] = Field(default_factory=list, description="Additional headers like index, mode, etc.")
    
    def __repr__(self) -> str:
        return f"DiffFile(path='{self.filepath}', hunks={len(self.hunks)})"
    
    @computed_field
    @property
    def filepath(self) -> str:
        path = self.new_path if self.new_path != '/dev/null' else self.old_path
        if path.startswith('a/') or path.startswith('b/'):
            return path[2:]
        return path
    
    
    @computed_field
    @property
    def messages(self) -> List[str]:
        return [line for line in self.header_lines if not line.startswith("index ")]
    
    
    @computed_field
    @property
    def rename_from(self) -> str | None:
        for line in self.messages:
            if line.startswith("rename from "):
                return line[len("rename from ") :]
        return None
    
    
    @computed_field
    @property
    def rename_to(self) -> str | None:
        for line in self.messages:
            if line.startswith("rename to "):
                return line[len("rename to ") :]
        return None
    
    
    @computed_field
    @property
    def is_new_file(self) -> bool:
        return any(line.startswith("new file mode ") for line in self.messages)
    
    
    @property
    def content(self) -> str:
        lines = [self.header]
        
        if self.header_lines:
            lines.extend(self.header_lines)
        
        # Header-only blocks (e.g. pure renames) have no ---/+++ or hunks
        if self.hunks:
            lines.append(f"--- {self.old_path}")
            lines.append(f"+++ {self.new_path}")
            for hunk in self.hunks:
                lines.append(hunk.content)
        
        return '\n'.join(lines)
    
