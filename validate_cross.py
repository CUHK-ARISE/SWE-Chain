import os
from pathlib import Path
from typing import List, Tuple
from argparse import ArgumentParser
from packaging.version import Version
from concurrent.futures import ThreadPoolExecutor, as_completed

from validation import TestRunResult, run_cross_tests
from evaluation.comparator import VersionComparator
from config import ORACLE_DIR, IMAGE_PREFIX, PACKAGES_ROOT, get_testing_folder, get_protected_dirs, get_protected_root_files, get_exec_timeout, get_excluded_versions


args = ArgumentParser(description="Run cross-version tests for adjacent pairs in a chain.")
args.add_argument("chain_dir", type=str, help="Chain directory (e.g., metadata/xarray_2022.11.0_to_2023.7.0)")
args.add_argument("--workers", default=5, type=int, help="Number of version pairs to process in parallel")
args = args.parse_args()

CHAIN_DIR = Path(args.chain_dir)
PACKAGE = CHAIN_DIR.name.split("_")[0]
OUTPUT_DIR = ORACLE_DIR / CHAIN_DIR.name
MAX_WORKERS = args.workers
EXCLUDED = get_excluded_versions(PACKAGE)

testing_folder = get_testing_folder(PACKAGE)
PACKAGE_DIR = PACKAGES_ROOT / PACKAGE


def get_upgrade_chain(versions: List[str]) -> List[Tuple[Version, Version]]:
    versions = [Version(v) for v in versions if v != ".DS_Store" and v not in EXCLUDED]
    versions.sort()
    return list(zip(versions, versions[1:]))


version_pairs = get_upgrade_chain(os.listdir(PACKAGE_DIR))


def _process_pair(v1: Version, v2: Version):
    savedir = OUTPUT_DIR / str(v2)
    savedir.mkdir(parents=True, exist_ok=True)
    
    try:
        baseline = TestRunResult.load(savedir / f"v{v2}_test_results.json")
        results = run_cross_tests(
            image=f"{IMAGE_PREFIX}:{PACKAGE}-{v2}",
            source=PACKAGE_DIR / str(v1),
            tests_rel=testing_folder,
            baseline=baseline,
            prefix="swe-cross",
            retry_workers=3,
            exec_timeout=get_exec_timeout(PACKAGE),
            extra_protected=[*get_protected_dirs(PACKAGE), *get_protected_root_files(PACKAGE)],
        )
        results.save(savedir / f"v{v1}_cross_test_results.json")
        cmp = VersionComparator(baseline, results)
        s = cmp.summary
        base_nodes = {k.split("[")[0] for k in baseline.results}
        cross_nodes = {k.split("[")[0] for k in results.results}
        cov = f"{len(cross_nodes)}/{len(base_nodes)}"
        
        if cross_nodes != base_nodes:
            cov = f"\033[33m{cov}\033[0m"
        
        unique_errors = len({
            (results.results[n].message or "").strip()
            for n in cmp.e2p
            if (results.results[n].message or "").strip()
        })
        e2p_str = f"{s['error_to_pass']} (error={unique_errors})"
        
        return v1, v2, "ok", (
            f"P2P={s['pass_to_pass']:<4} F2P={s['fail_to_pass']:<4} "
            f"E2P={e2p_str:<14} UR={s['upgrade_related']:<4} "
            f"P2F={s['pass_to_fail']:<4} F2F={s['fail_to_fail']:<4} "
            f"COV={cov}"
        )
    except Exception as e:
        return v1, v2, "fail", str(e)


pairs = list(version_pairs)
print(f"Processing {len(pairs)} version pairs...")

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(_process_pair, v1, v2): (v1, v2) for v1, v2 in pairs}
    for future in as_completed(futures):
        v1, v2, status, msg = future.result()
        if status == "fail":
            print(f"  [WARN] {f'{str(v1)} -> {str(v2)}:':<20} {msg}")
        else:
            print(f"  [DONE] {f'{str(v1)} -> {str(v2)}:':<20} {msg}")
