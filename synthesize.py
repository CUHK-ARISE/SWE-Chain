from __future__ import annotations
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List
from packaging.version import Version

from synthetic import (
    CompletingTaskRunner,
    ConquerTaskRunner,
    HunkMatchingRunner,
    SpecsExtractor,
)


def parse_args():
    p = argparse.ArgumentParser(description="Synthesize specs for a collected chain.")
    p.add_argument("chain_dir", type=Path, help="Chain directory (e.g., metadata/attrs_21.3.0_to_26.1.0)")
    p.add_argument("--model", default="gpt-5.4", help="Codex model for matching/conquer/completing")
    p.add_argument("--workers", default=4, type=int, help="Parallel workers for hunk matching")
    p.add_argument("--granularity", choices=["conceptual", "grounded"], default="conceptual")
    p.add_argument("--include-criteria", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--include-github", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--include-behavioral", action=argparse.BooleanOptionalAction, default=False)
    return p.parse_args()


def version_pairs(chain_dir: Path) -> List[tuple]:
    versions = sorted(
        (d.name for d in chain_dir.iterdir() if d.is_dir()),
        key=Version,
    )
    return list(zip(versions, versions[1:]))


def run_matching(model: str, chain_dir: Path, workers: int) -> None:
    pairs = version_pairs(chain_dir)
    print(f"\n[1/3] Hunk matching for {len(pairs)} pairs (model={model}, workers={workers})...")
    
    def _process(v1: str, v2: str):
        ver_dir = chain_dir / v2
        diff_dir = ver_dir / "differences"
        metadata = ver_dir / "metadata"
        savedir = ver_dir / "synthetic"
        savedir.mkdir(parents=True, exist_ok=True)
        release_note = metadata / f"v{v2}_changelog_tasks.json"
        for diff_name, output_name in [
            (f"code_diff_v{v1}_to_v{v2}_py.diff", f"v{v2}_tasks_code_hunks.json"),
            (f"test_diff_v{v1}_to_v{v2}.diff",    f"v{v2}_tasks_tests_hunks.json"),
        ]:
            diff_path = diff_dir / diff_name
            if not diff_path.exists():
                print(f"  [skip] {v1} -> {v2}: {diff_name} not found")
                continue
            HunkMatchingRunner(
                diff_path=diff_path,
                release_note_path=release_note,
                output_path=savedir / output_name,
                model=model,
                max_files=20, max_hunks=150, max_lines=3000,
                continuous=True,
            ).run()
        return f"{v1} -> {v2}"
    
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, v1, v2): (v1, v2) for v1, v2 in pairs}
        for future in as_completed(futures):
            v1, v2 = futures[future]
            try:
                print(f"  matched {future.result()}")
            except Exception as e:
                print(f"  [error] {v1} -> {v2}: {e}")


def run_conquer(model: str, chain_dir: Path) -> None:
    pairs = version_pairs(chain_dir)
    print(f"\n[2/3] Conquer + completing for {len(pairs)} pairs (model={model})...")
    
    for v1, v2 in pairs:
        ver_dir = chain_dir / v2
        diff_dir = ver_dir / "differences"
        metadata = ver_dir / "metadata"
        savedir = ver_dir / "synthetic"
        savedir.mkdir(parents=True, exist_ok=True)
        code_diff = diff_dir / f"code_diff_v{v1}_to_v{v2}_py.diff"
        test_diff = diff_dir / f"test_diff_v{v1}_to_v{v2}.diff"
        release_note = metadata / f"v{v2}_changelog_tasks.json"
        code_match = savedir / f"v{v2}_tasks_code_hunks.json"
        test_match = savedir / f"v{v2}_tasks_tests_hunks.json"
        specs_path = savedir / f"v{v2}_specs.json"
        new_tasks_path = savedir / f"v{v2}_new_tasks.json"
        
        if not code_match.exists():
            print(f"  [skip] {v1} -> {v2}: matching results not found")
            continue
        
        ConquerTaskRunner(
            old_version=v1, new_version=v2,
            release_note_path=release_note,
            code_diff_path=code_diff, code_matching_path=code_match,
            test_diff_path=test_diff, test_matching_path=test_match,
            output_path=specs_path,
            model=model, workers=3, continuous=True,
        ).run()
        
        CompletingTaskRunner(
            old_version=v1, new_version=v2,
            code_diff_path=code_diff, code_matching_path=code_match,
            test_diff_path=test_diff if test_diff.exists() else None,
            test_matching_path=test_match if test_match.exists() else None,
            synthesized_path=specs_path, output_path=new_tasks_path,
            model=model, continuous=True,
        ).run()


def assemble_specs(chain_dir: Path, granularity: str, include_criteria: bool, include_github: bool, include_behavioral: bool) -> Path:
    package = chain_dir.name.split("_")[0]
    pairs = version_pairs(chain_dir)
    print(f"\n[3/3] Assembling specs for {len(pairs)} pairs...")
    
    rows = []
    for v1, v2 in pairs:
        ver_dir = chain_dir / v2
        diff_dir = ver_dir / "differences"
        metadata = ver_dir / "metadata"
        savedir = ver_dir / "synthetic"
        base_specs = savedir / f"v{v2}_specs.json"
        extra_specs = savedir / f"v{v2}_new_tasks.json"
        raw_tasks = metadata / f"v{v2}_changelog_tasks.json"
        output_md = savedir / f"v{v2}_specs.md"

        if not base_specs.exists():
            print(f"  [skip] {v1} -> {v2}: {base_specs.name} not found")
            continue

        SpecsExtractor(
            old_version=v1, new_version=v2,
            base_specs_path=str(base_specs),
            raw_tasks_path=str(raw_tasks),
            diff_path=str(diff_dir / f"code_diff_v{v1}_to_v{v2}_py.diff"),
            extra_specs_path=str(extra_specs),
        ).run(
            output_path=str(output_md),
            granularity=granularity,
            include_criteria=include_criteria,
            include_github=include_github,
            include_behavioral=include_behavioral,
        )
        
        rows.append({
            "package": package,
            "prev_ver": v1,
            "next_ver": v2,
            "specs": output_md.read_text(),
        })
    
    chain_jsonl = Path("data") / f"{chain_dir.name}_specs_chain.jsonl"
    chain_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(chain_jsonl, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return chain_jsonl


def main():
    args = parse_args()
    chain_dir: Path = args.chain_dir
    if not chain_dir.is_dir():
        raise SystemExit(f"Chain dir not found: {chain_dir}")
    print(f"Chain: {chain_dir.name}")
    run_matching(args.model, chain_dir, args.workers)
    run_conquer(args.model, chain_dir)
    out = assemble_specs(chain_dir, args.granularity, args.include_criteria, args.include_github, args.include_behavioral)
    print(f"\nDone. Specs chain at {out}")


if __name__ == "__main__":
    main()
