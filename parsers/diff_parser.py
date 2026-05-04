from pathlib import Path
from typing import List, Optional, Tuple

from .diff_file import DiffFile
from .diff_hunk import DiffHunk


class DiffParser:
    def __init__(self, path: str):
        self.path = Path(path)
        self.files: List[DiffFile] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except FileNotFoundError:
            raise FileNotFoundError(f"Diff file not found: {self.path}")
        self.parse_lines(lines)
    
    
    @classmethod
    def from_string(cls, content: str) -> "DiffParser":
        instance = cls.__new__(cls)
        instance.path = None
        instance.files = []
        instance.parse_lines(content.splitlines(keepends=True))
        return instance
    
    
    def __repr__(self) -> str:
        return f"DiffParser(path='{self.path}', files={len(self.files)})"
    
    
    def parse_lines(self, lines: List[str]):
        current_file: Optional[DiffFile] = None
        current_hunk: Optional[DiffHunk] = None
        file_header: str = ""
        pending_header_lines: List[str] = []
        
        i = 0
        while i < len(lines):
            line = lines[i].rstrip('\n')
            
            # 1) new file block starts with "diff --git"
            if line.startswith('diff --git'):
                # Save the previous file and hunk before starting a new file diff
                if current_file and current_hunk:
                    current_file.hunks.append(current_hunk)
                if current_file:
                    self.files.append(current_file)
                elif file_header and pending_header_lines:
                    # Header-only block (e.g. pure rename with no ---/+++ or hunks)
                    old_path, new_path = self.paths_from_header_lines(file_header, pending_header_lines)
                    self.files.append(DiffFile(
                        header=file_header, old_path=old_path, new_path=new_path,
                        header_lines=pending_header_lines,
                    ))
                
                file_header = line
                pending_header_lines = []
                
                # Reset for the new file
                current_file = None
                current_hunk = None
                i += 1
                continue
            
            # 2) file paths are indicated by "---" and "+++" lines
            if line.startswith('---'):
                if current_file and current_hunk:
                    current_file.hunks.append(current_hunk)
                    current_hunk = None
                
                old_path = line[4:].strip()  # Remove '--- '
                
                if i + 1 < len(lines) and lines[i + 1].startswith('+++'):
                    new_path = lines[i + 1][4:].strip()  # Remove '+++ '
                    i += 1
                else:
                    new_path = old_path
                
                if current_file is None:
                    current_file = DiffFile(
                        header=file_header, old_path=old_path, new_path=new_path,
                        header_lines=pending_header_lines,
                    )
                    pending_header_lines = []
                else:
                    current_file.old_path = old_path
                    current_file.new_path = new_path
            
            # 3) hunk header starts with "@@"
            elif line.startswith('@@'):
                if current_hunk and current_file:
                    current_file.hunks.append(current_hunk)
                current_hunk = DiffHunk(raw_header=line)
            
            # 4) hunk content (lines starting with +, -, or space)
            elif current_hunk is not None:
                if line.startswith('+') or line.startswith('-') or line.startswith(' '):
                    current_hunk.lines.append(line)
                elif line == '':
                    current_hunk.lines.append(line)
            
            # 5) lines between file header and first hunk (e.g., index, new file mode, deleted file mode, etc.)
            elif file_header and current_file is None:
                pending_header_lines.append(line)
            
            i += 1
        
        # Save the last hunk and file
        if current_hunk and current_file:
            current_file.hunks.append(current_hunk)
        if current_file:
            self.files.append(current_file)
        elif file_header and pending_header_lines:
            old_path, new_path = self.paths_from_header_lines(file_header, pending_header_lines)
            self.files.append(DiffFile(
                header=file_header, old_path=old_path, new_path=new_path,
                header_lines=pending_header_lines,
            ))
    
    
    @staticmethod
    def paths_from_header_lines(file_header: str, header_lines: List[str]) -> Tuple[str, str]:
        """Extract old/new paths from header-only blocks (e.g. pure renames)."""
        rename_from = rename_to = None
        for hl in header_lines:
            if hl.startswith("rename from "):
                rename_from = hl[len("rename from "):]
            elif hl.startswith("rename to "):
                rename_to = hl[len("rename to "):]
        if rename_from and rename_to:
            return f"a/{rename_from}", f"b/{rename_to}"
        # Fallback: parse from "diff --git a/X b/Y"
        parts = file_header.split(" b/", 1)
        path = parts[1] if len(parts) > 1 else ""
        return f"a/{path}", f"b/{path}"
    
    
    def get_file_by_header(self, file_header: str) -> Optional[DiffFile]:
        """Find the DiffFile object corresponding to the given file header (e.g., `diff --git a/file.txt b/file.txt`)."""
        for file in self.files:
            if file.header == file_header:
                return file
        return None
    
    
    def check_hunks_exist(self, file_header: str, hunk_headers: List[str]) -> bool:
        """Check if all specified hunk headers exist in the given file."""
        target_file = self.get_file_by_header(file_header)
        if target_file is None:
            return False
        existing_headers = {hunk.header for hunk in target_file.hunks}
        return all(header in existing_headers for header in hunk_headers)
    
    
    def get_diff_by_hunk_headers(self, file_header: str, hunk_headers: List[str], keep_index: bool = False) -> str:
        """Reconstruct a diff string for selected hunks of a file."""
        target_file = self.get_file_by_header(file_header)
        if target_file is None:
            return ""
        
        hunk_set = set(hunk_headers)
        selected_hunks = [h for h in target_file.hunks if h.header in hunk_set]
        
        lines = [target_file.header]
        if target_file.header_lines:
            lines.extend(l for l in target_file.header_lines if keep_index or not l.startswith('index '))
        
        lines.append(f"--- {target_file.old_path}")
        lines.append(f"+++ {target_file.new_path}")
        
        for hunk in selected_hunks:
            lines.append(hunk.content)
        
        return '\n'.join(lines)
    
    
