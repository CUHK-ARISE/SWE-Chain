import subprocess
from typing import List
from packaging.version import Version
from fetchers.package import PackageExtractor
from concurrent.futures import ThreadPoolExecutor, as_completed

from validation.utils import image_exists, get_dockerfile
from config import PACKAGES_ROOT, IMAGE_PREFIX, get_source


def build_one(package: str, version: Version) -> str:
    tag = f"{IMAGE_PREFIX}:{package}-{version}"
    if image_exists(tag):
        return f"  [skip] {tag}"
    source = PACKAGES_ROOT / package / str(version)
    dockerfile, build_args = get_dockerfile(package, version)
    flags = [a for k, v in build_args.items() for a in ("--build-arg", f"{k}={v}")]
    r = subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(dockerfile), *flags, str(source)],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-10:]
        raise RuntimeError(f"docker build failed for {tag}:\n" + "\n".join(tail))
    return f"  [built] {tag}"


def prepare_chain(package: str, versions: List[str], workers: int = 3) -> None:
    versions = sorted(Version(v) for v in versions)
    
    print(f"Preparing {package}: {len(versions)} versions ({versions[0]} -> {versions[-1]})")
    print("[1/2] Downloading sources ...")
    PackageExtractor(name=package).download(
        versions=versions, save_dir=str(PACKAGES_ROOT), source=get_source(package),
    )
    
    print("[2/2] Building docker images ...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(build_one, package, v): v for v in versions}
        for fut in as_completed(futs):
            print(fut.result())
