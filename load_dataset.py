from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import pyarrow.parquet as pq
from huggingface_hub import snapshot_download

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
HF_REPO_ID = "For-Anonymous-Submission-90/SWE-Chain"


def explode_parquet(parquet_path: Path, data_dir: Path) -> int:
    rows = pq.read_table(parquet_path).to_pylist()
    by_chain: dict[str, list] = {}
    for r in rows:
        by_chain.setdefault(r["chain_id"], []).append(r)
    
    data_dir.mkdir(parents=True, exist_ok=True)
    for chain_id, chain_rows in by_chain.items():
        chain_rows.sort(key=lambda r: r["step_id"])
        out = data_dir / f"{chain_id}_specs_chain.jsonl"
        with open(out, "w") as f:
            for r in chain_rows:
                f.write(json.dumps({
                    "package": r["package"],
                    "prev_ver": r["prev_ver"],
                    "next_ver": r["next_ver"],
                    "specs": r["specs"],
                }, ensure_ascii=False) + "\n")
    return len(by_chain)


def main():
    snap = Path(snapshot_download(repo_id=HF_REPO_ID, repo_type="dataset", allow_patterns=["data/*.parquet"]))
    parquet_path = snap / "data" / "specs_chain.parquet"
    
    if not parquet_path.exists():
        sys.exit(f"expected parquet not found: {parquet_path}")
        
    data_dir = Path("data")
    n_chains = explode_parquet(parquet_path, data_dir)
    
    local_oracle = Path("oracle")
    n_oracle = sum(1 for _ in local_oracle.rglob("*.json")) if local_oracle.is_dir() else 0
    
    print(f"\nSnapshot:    {snap}")
    print(f"Chains:      {n_chains} -> {data_dir}/<chain>_specs_chain.jsonl")
    
    print("\nReady. Next steps:")
    print("  bash run.sh   data/<chain>_specs_chain.jsonl <agent> <provider> <model>")
    print("  bash eval.sh  results/.../chain.json")


if __name__ == "__main__":
    main()
