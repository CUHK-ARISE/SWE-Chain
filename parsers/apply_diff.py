import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple, Union


def parse_special_operations(diff_content: str) -> Tuple[List, List[Tuple]]:
    deleted_files = []
    renames = []
    current_file = None
    is_deleted = False
    rename_from = None
    
    for line in diff_content.splitlines():
        if line.startswith("diff --git "):
            # Flush previous file
            if current_file and is_deleted:
                deleted_files.append(current_file)
            if rename_from and current_file:
                renames.append((rename_from, current_file))
            
            # Parse path from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            current_file = parts[1].lstrip("/") if len(parts) > 1 else None
            is_deleted = False
            rename_from = None
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("rename from "):
            rename_from = line[len("rename from "):].lstrip("/")
        elif line.startswith("rename to "):
            current_file = line[len("rename to "):].lstrip("/")
    
    # Flush last file
    if current_file and is_deleted:
        deleted_files.append(current_file)
    if rename_from and current_file:
        renames.append((rename_from, current_file))
    return deleted_files, renames


def safe_unlink(path: Union[str, Path]) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def apply_diff(codebase: str, diff_content: str) -> None:
    if not diff_content.strip():
        return
    
    codebase_path = Path(codebase).resolve()
    deleted_files, renames = parse_special_operations(diff_content)
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False, encoding="utf-8") as f:
        f.write(diff_content)
        diff_file = f.name

    try:
        try:
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", diff_file],
                cwd=str(codebase_path),
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to patch if git apply fails or git is unavailable.
            try:
                subprocess.run(
                    ["patch", "-p1", "-i", diff_file],
                    cwd=str(codebase_path),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to apply diff: {(e.stderr or e.stdout).strip()}")
            except FileNotFoundError as e:
                raise RuntimeError(
                    "Failed to apply diff: neither 'git' nor 'patch' is available"
                ) from e
    finally:
        safe_unlink(diff_file)
    
    # Handle deletions (patch doesn't remove files with "deleted file mode")
    for rel_path in deleted_files:
        full_path = codebase_path / rel_path
        if full_path.exists():
            full_path.unlink()
    
    # Handle renames (patch doesn't understand "rename from/to")
    for old_rel, new_rel in renames:
        old_path = codebase_path / old_rel
        new_path = codebase_path / new_rel
        if old_path.exists():
            if not new_path.exists():
                # Pure rename (no content change) — patch didn't create new file
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(old_path), str(new_path))
            old_path.unlink()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Apply a diff file to a codebase directory.")
    parser.add_argument("codebase", help="Path to the codebase directory")
    parser.add_argument("diff", help="Path to the .diff file to apply")
    args = parser.parse_args()
    diff_content = Path(args.diff).read_text(encoding="utf-8")
    apply_diff(args.codebase, diff_content)
    print(f"Diff applied to {args.codebase}")
