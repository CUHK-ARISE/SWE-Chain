import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Union

from .diff_parser import DiffParser
from .node import NodeID
from config import SKIP_DIRS


class DiffExtractor:
    def __init__(self, old_dir: str, new_dir: str):
        self.old_dir = Path(old_dir)
        if not self.old_dir.exists() or not self.old_dir.is_dir():
            raise ValueError(f"Old path {old_dir} is not a directory")
        
        self.new_dir = Path(new_dir)
        if not self.new_dir.exists() or not self.new_dir.is_dir():
            raise ValueError(f"New path {new_dir} is not a directory")
    
    
    @staticmethod
    def run_git_diff(old_dir: Path, new_dir: Path) -> str:
        """Run git diff --no-index on two directories. Returns raw diff output."""
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--no-index", "--no-color", str(old_dir), str(new_dir)],
            capture_output=True, text=True,
        )
        # exit code 0 = no diffs, 1 = diffs found, >1 = error
        if result.returncode > 1:
            raise RuntimeError(f"git diff failed (exit {result.returncode}): {result.stderr.strip()}")
        return result.stdout
    
    
    @staticmethod
    def rewrite_paths(diff_output: str, old_dir: Path, new_dir: Path) -> str:
        """Rewrite absolute paths in diff headers and rename lines to be relative to diff roots."""
        old_prefix = str(old_dir).lstrip("/") + "/"
        new_prefix = str(new_dir).lstrip("/") + "/"
        lines = diff_output.split("\n")
        out = []
        for line in lines:
            # e.g., `a/.packages/flask/2.0.0/src/flask/app.py` -> `a/src/flask/app.py`
            if line.startswith(("diff --git ", "--- ", "+++ ")):
                line = line.replace(old_prefix, "").replace(new_prefix, "")
            
            # e.g., `rename from /a/.packages/flask/2.0.0/src/flask/app.py` -> `rename from a/src/flask/app.py`
            elif line.startswith("rename from ") or line.startswith("rename to "):
                line = line.replace(old_prefix, "").replace(new_prefix, "")
                parts = line.split(" ", 2)
                if len(parts) == 3:
                    parts[2] = parts[2].lstrip("/")
                    line = " ".join(parts)
            out.append(line)
        return "\n".join(out)
    
    
    @staticmethod
    def should_include_file(rel_path: str, match_paths: List[str], match_exts: List[str], exclude_paths: List[str], exclude_exts: List[str]) -> bool:
        parts = Path(rel_path).parts
        if any(part in SKIP_DIRS for part in parts):
            return False
        if any(rel_path.startswith(ep) or rel_path == ep for ep in exclude_paths):
            return False
        if any(rel_path.endswith(ee) for ee in exclude_exts):
            return False
        if match_paths and not any(rel_path.startswith(mp) for mp in match_paths):
            return False
        if match_exts and not any(rel_path.endswith(me) for me in match_exts):
            return False
        return True
    
    
    @staticmethod
    def changed_new_lines(hunk) -> List[int]:
        """Get new-file line numbers that have actual changes (+ or - lines)."""
        new_start = hunk.line_range[2]
        lines: List[int] = []
        cur = new_start
        for line in hunk.lines:
            if line.startswith("+"):
                lines.append(cur)
                cur += 1
            elif line.startswith("-"):
                lines.append(cur)
            else:
                cur += 1
        return lines
    
    
    def create_structured_diff(self, diff_content: str) -> Dict[str, Dict]:
        """Parse diff content and create structured representation."""
        parser = DiffParser.from_string(diff_content)
        structured_diff = {}
        
        for diff_file in parser.files:
            file_path = diff_file.filepath
            hunks_data = []
            
            analyzer = None
            if file_path.endswith(".py"):
                full_path = self.new_dir / file_path
                if full_path.exists():
                    from .ast_analyzer import PythonASTAnalyzer
                    analyzer = PythonASTAnalyzer(full_path, display_path=file_path)
            
            for hunk in diff_file.hunks:
                snippet_lines = []
                if hunk.context:
                    snippet_lines.append(f" {hunk.context}")
                snippet_lines.extend(hunk.lines)
                hunk_info = {
                    "hunk": hunk.header,
                    "snippet": "\n".join(snippet_lines),
                }
                
                if analyzer and analyzer.tree:
                    changed_lines = self.changed_new_lines(hunk)
                    if changed_lines:
                        ast_nodes = analyzer.get_all_nodes_for_lines(changed_lines)
                        if ast_nodes:
                            node_ids = [analyzer.get_qualified_name(n) for n in ast_nodes]
                        else:
                            node_ids = [NodeID.module(file_path)]
                    else:
                        node_ids = [NodeID.module(file_path)]
                else:
                    node_ids = [NodeID.file(file_path)]
                
                seen = set()
                unique_ids = []
                for nid in node_ids:
                    key = str(nid)
                    if key not in seen:
                        seen.add(key)
                        unique_ids.append(nid)
                node_ids = unique_ids
                
                hunk.nodes = node_ids
                hunk_info["affect"] = [n.to_dict() for n in node_ids]
                hunks_data.append(hunk_info)
            messages = [l for l in diff_file.header_lines if not l.startswith("index ")]
            file_entry = {"hunks": hunks_data}
            if messages:
                file_entry["messages"] = messages
            structured_diff[file_path] = file_entry
        return structured_diff
    
    
    def extract_diff(self, match_paths: List[str] = [], match_exts: List[str] = [], exclude_paths: List[str] = [], exclude_exts: List[str] = []) -> Tuple[str, int]:
        # Clean path filters (e.g., "./src/" -> "src")
        match_paths = [s for p in match_paths if (s := p.strip("/").strip("./"))]
        exclude_paths = [s for p in exclude_paths if (s := p.strip("/").strip("./"))]
        
        # Run git diff and get raw output
        raw = self.run_git_diff(self.old_dir, self.new_dir)
        raw = self.rewrite_paths(raw, self.old_dir, self.new_dir)
        
        # Split into per-file segments at "diff --git" boundaries
        segments = re.split(r"(?=^diff --git )", raw, flags=re.MULTILINE)
        segments = [s for s in segments if s.strip()]
        
        # Filter segments by filepath
        kept = []
        for seg in segments:
            m = re.match(r"diff --git a/(.+?) b/", seg)
            if not m:
                continue
            rel_path = m.group(1)
            if self.should_include_file(rel_path, match_paths, match_exts, exclude_paths, exclude_exts):
                kept.append(seg.rstrip("\n") + "\n")
        
        if not kept:
            return "", 0
        return "\n".join(kept), len(kept)
    
    
    def save_diff(self, diff_content: str, output_dir: Union[str, Path], output_name: str) -> Dict:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        diff_path = output_dir / f"{output_name}.diff"
        with open(diff_path, "w", encoding="utf-8") as f:
            f.write(diff_content)
        
        structured_diff = self.create_structured_diff(diff_content)
        
        result = {
            "old_dir": str(self.old_dir),
            "new_dir": str(self.new_dir),
            "extracted_at": datetime.now().isoformat(),
            "diff_file": str(diff_path),
            "diff_content": diff_content,
        }
        
        if structured_diff:
            result["structured_diff"] = structured_diff
        
        json_path = output_dir / f"{output_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"  Saved: {diff_path} and {json_path}")
        
        return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract structured diffs between two directory trees.")
    
    # Positional arguments for old and new directories
    parser.add_argument("old_dir", help="Path to the old directory")
    parser.add_argument("new_dir", help="Path to the new directory")
    
    # Specify output directory and base filename for .diff and .json outputs
    parser.add_argument("--output-dir", default="diff_output", help="Directory to save output files")
    parser.add_argument("--output-name", required=True, help="Base filename for output .diff and .json files")
    
    # Filtering options
    parser.add_argument("--match-paths", nargs="+", default=[], help="Only include files under this path prefix")
    parser.add_argument("--match-exts", nargs="+", default=[], help="Only include files ending with these extensions")
    parser.add_argument("--exclude-paths", nargs="+", default=[], help="Exclude paths/files by prefix or exact name")
    parser.add_argument("--exclude-exts", nargs="+", default=[], help="Exclude files by extensions")
    
    args = parser.parse_args()
    
    extractor = DiffExtractor(args.old_dir, args.new_dir)
    
    diff_content, files_changed = extractor.extract_diff(
        match_paths=args.match_paths,
        match_exts=args.match_exts,
        exclude_paths=args.exclude_paths,
        exclude_exts=args.exclude_exts,
    )
    
    print(f"  {files_changed} files changed")
    
    extractor.save_diff(
        diff_content=diff_content,
        output_dir=args.output_dir,
        output_name=args.output_name,
    )
