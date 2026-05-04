from pathlib import Path
from argparse import ArgumentParser
from packaging.version import Version
from concurrent.futures import ThreadPoolExecutor, as_completed

from validation import run_original_tests
from validation.utils import image_exists
from config import ORACLE_DIR, IMAGE_PREFIX, PACKAGES_ROOT, get_testing_folder, get_exec_timeout, get_excluded_versions


def process(package: str, package_dir: Path, output_dir: Path, version: Version, force: bool):
    tag = f"{IMAGE_PREFIX}:{package}-{version}"
    source = package_dir / str(version)
    result_path = output_dir / str(version) / f"v{version}_test_results.json"
    
    if not image_exists(tag):
        return version, f"SKIP (image {tag} not found; run build.py first)"
    
    if not force and result_path.exists():
        return version, f"skipped (results exist: {result_path})"
    
    result = run_original_tests(
        image=tag,
        source=source,
        tests_rel=get_testing_folder(package),
        retry_workers=3,
        exec_timeout=get_exec_timeout(package),
    )
    result.save(result_path)
    return version, f"{len(result.passed)} passed, {len(result.failed)} failed, {len(result.errors)} errors, {len(result.skipped)} skipped"


def main():
    parser = ArgumentParser(description="Run version-local baseline tests for every version in a chain.")
    parser.add_argument("chain_dir", type=str, help="Chain directory (e.g., metadata/xarray_2022.11.0_to_2023.7.0)")
    parser.add_argument("--workers", default=5, type=int, help="Number of versions to process in parallel")
    parser.add_argument("-f", "--force", action="store_true", help="Re-run tests even if results JSON already exists")
    args = parser.parse_args()
    
    chain_dir = Path(args.chain_dir)
    package = chain_dir.name.split("_")[0]
    package_dir = PACKAGES_ROOT / package
    output_dir = ORACLE_DIR / chain_dir.name
    excluded = get_excluded_versions(package)
    
    versions = sorted(
        Version(d.name) for d in package_dir.iterdir()
        if d.is_dir() and d.name != ".DS_Store" and d.name not in excluded
    )
    
    print(f"Testing {len(versions)} versions with {args.workers} workers...")
    
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, package, package_dir, output_dir, v, args.force): v for v in versions}
        for future in as_completed(futures):
            v = futures[future]
            try:
                ver, msg = future.result()
            except Exception as e:
                ver, msg = v, f"ERROR: {e}"
            print(f"  {ver}: {msg}")


if __name__ == "__main__":
    main()
