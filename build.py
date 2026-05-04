import subprocess
from pathlib import Path
from argparse import ArgumentParser
from packaging.version import Version
from concurrent.futures import ThreadPoolExecutor, as_completed

from validation.utils import image_exists, get_dockerfile
from config import IMAGE_PREFIX, PACKAGES_ROOT, get_excluded_versions


def process(package: str, package_dir: Path, version: Version, force: bool):
    tag = f"{IMAGE_PREFIX}:{package}-{version}"
    source = package_dir / str(version)
    
    if not force and image_exists(tag):
        return version, f"skipped (image exists: {tag})"
    
    dockerfile, build_args = get_dockerfile(package, version)
    build_arg_flags = [a for k, v in build_args.items() for a in ("--build-arg", f"{k}={v}")]
    build = subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(dockerfile), *build_arg_flags, str(source)],
        capture_output=True, text=True, timeout=600,
    )
    if build.returncode != 0:
        tail = (build.stderr or build.stdout or "").strip().splitlines()[-20:]
        return version, "BUILD FAIL:\n" + "\n".join(f"    {l}" for l in tail)
    return version, f"built ({tag})"


def main():
    parser = ArgumentParser(description="Build docker images for every version in a chain.")
    parser.add_argument("chain_dir", type=str, help="Chain directory (e.g., metadata/xarray_2022.11.0_to_2023.7.0)")
    parser.add_argument("--workers", default=5, type=int, help="Number of versions to build in parallel")
    parser.add_argument("-f", "--force", action="store_true", help="Rebuild even if image already exists")
    args = parser.parse_args()
    
    chain_dir = Path(args.chain_dir)
    package = chain_dir.name.split("_")[0]
    package_dir = PACKAGES_ROOT / package
    excluded = get_excluded_versions(package)
    
    versions = sorted(
        Version(d.name) for d in package_dir.iterdir()
        if d.is_dir() and d.name != ".DS_Store" and d.name not in excluded
    )
    
    print(f"Building {len(versions)} versions with {args.workers} workers...")
    
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, package, package_dir, v, args.force): v for v in versions}
        for future in as_completed(futures):
            v = futures[future]
            try:
                ver, msg = future.result()
            except Exception as e:
                ver, msg = v, f"ERROR: {e}"
            print(f"  {ver}: {msg}")


if __name__ == "__main__":
    main()
