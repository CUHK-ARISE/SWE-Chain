from __future__ import annotations
from typing import List
from pathlib import Path
from argparse import ArgumentParser
from packaging.version import Version

from fetchers.changelog_fetcher import FETCHER_MAP
from fetchers.package import PackageExtractor
from fetchers.release_note_extractor import extract_release_note_items
from parsers.diff_extractor import DiffExtractor
from config import METADATA_DIR, PACKAGES_ROOT, get_excluded_versions, get_source, get_testing_folder


def resolve_versions(package: str, lo: Version, hi: Version) -> List[Version]:
    """Return canonical-form versions in [lo, hi], excluding YAML-listed exclusions."""
    excluded = get_excluded_versions(package)
    extractor = PackageExtractor(name=package)
    available = extractor.get_available_versions(min_major=lo.major, min_minor=lo.minor)
    out: List[Version] = []
    for v in available:
        if not (lo <= v <= hi):
            continue
        if str(v) in excluded:
            print(f"  [skip] {package} {v}: {excluded[str(v)]}")
            continue
        out.append(v)
    out.sort()
    return out


def download_sources(package: str, versions: List[Version]) -> None:
    source = get_source(package)
    extractor = PackageExtractor(name=package)
    print(f"\n[1/4] Downloading {len(versions)} versions of {package} from {source}...")
    extractor.download(versions=versions, source=source)


def fetch_changelogs(package: str, versions: List[Version], chain_dir: Path) -> None:
    print(f"\n[2/4] Fetching changelogs for {len(versions)} versions...")
    if package not in FETCHER_MAP:
        raise ValueError(f"No changelog fetcher registered for '{package}' in FETCHER_MAP")
    results = FETCHER_MAP[package](versions=versions)
    for v, data in results.items():
        if data is None:
            print(f"  [missing] no changelog for {package} {v}")
            continue
        version_dir = chain_dir / str(v) / "metadata"
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / f"v{v}_changelog.md").write_text(data["content"], encoding="utf-8")


def extract_tasks(chain_dir: Path) -> None:
    md_paths = sorted(chain_dir.glob("*/metadata/v*_changelog.md"))
    print(f"\n[3/4] Extracting release-note tasks from {len(md_paths)} changelogs...")
    for md_path in md_paths:
        output_path = md_path.with_name(f"{md_path.stem}_tasks.json")
        extract_release_note_items(md_path, output_path)


def extract_diffs(package: str, versions: List[Version], chain_dir: Path) -> None:
    testing_folder = get_testing_folder(package)
    package_dir = PACKAGES_ROOT / package
    pairs = list(zip(versions, versions[1:]))
    print(f"\n[4/4] Extracting diffs for {len(pairs)} version pairs...")
    for v1, v2 in pairs:
        output_dir = chain_dir / str(v2) / "differences"
        extractor = DiffExtractor(package_dir / str(v1), package_dir / str(v2))
        for kwargs, name in [
            ({"match_exts": [".py"], "exclude_paths": [testing_folder]}, f"code_diff_v{v1}_to_v{v2}_py"),
            ({"match_exts": [".py"], "match_paths": [testing_folder]}, f"test_diff_v{v1}_to_v{v2}"),
            ({"exclude_exts": [".py"]}, f"others_diff_v{v1}_to_v{v2}"),
        ]:
            content, _ = extractor.extract_diff(**kwargs)
            extractor.save_diff(diff_content=content, output_dir=output_dir, output_name=name)
        print(f"  diffed {v1} -> {v2}")


def main():
    parser = ArgumentParser(description="Collect a chain: download sources, fetch changelogs, extract release-note tasks, and compute pairwise diffs.")
    parser.add_argument("package", help="Package name (must exist in packages.yaml + FETCHER_MAP)")
    parser.add_argument("--from", dest="from_ver", required=True, help="Min version (inclusive)")
    parser.add_argument("--to", dest="to_ver", required=True, help="Max version (inclusive)")
    args = parser.parse_args()
    
    package = args.package
    versions = resolve_versions(package, Version(args.from_ver), Version(args.to_ver))
    if len(versions) < 2:
        raise SystemExit(f"Need at least 2 versions to form a chain; got {len(versions)}")
    
    chain_name = f"{package}_{versions[0]}_to_{versions[-1]}"
    chain_dir = METADATA_DIR / chain_name
    chain_dir.mkdir(parents=True, exist_ok=True)
    print(f"Chain: {chain_name}  ({len(versions)} versions)")
    download_sources(package, versions)
    fetch_changelogs(package, versions, chain_dir)
    extract_tasks(chain_dir)
    extract_diffs(package, versions, chain_dir)
    print(f"\nDone. Chain materialized at {chain_dir}/")


if __name__ == "__main__":
    main()
