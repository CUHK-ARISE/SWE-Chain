from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

import yaml
from packaging.version import Version

REPO_ROOT = Path(__file__).parent

# Config and metadata paths
METADATA_DIR = REPO_ROOT / "metadata"
ORACLE_DIR = REPO_ROOT / "oracle"
PACKAGES_CONFIG_PATH = REPO_ROOT / "packages.yaml"

# Tmp dirs for intermediate files
PACKAGES_ROOT = REPO_ROOT / ".packages"
WORKSPACE_DIR = REPO_ROOT / ".workspace"
TMP_PATH = REPO_ROOT / ".tmp"

IMAGE_PREFIX = "swe-chain"
DOCKER_BASE_DIR = "/app/code"
DOCKER_SCRIPTS_DIR = REPO_ROOT / "validation" / "docker_scripts"

SKIP_DIRS = {"docs", "__pycache__", ".git", "htmlcov", ".eggs", ".pytest_cache"}


@lru_cache(maxsize=1)
def load() -> dict:
    return yaml.safe_load(PACKAGES_CONFIG_PATH.read_text())


def pkg(name: str) -> dict:
    return load().get("packages", {}).get(name, {})


def default(key: str, fallback):
    return load().get("defaults", {}).get(key, fallback)


def get_exec_timeout(package: str) -> int:
    return pkg(package).get("exec_timeout", default("exec_timeout", 1200))


def get_testing_folder(package: str) -> str:
    return pkg(package).get("testing_folder", default("testing_folder", "tests"))


def get_protected_dirs(package: str) -> List[str]:
    return pkg(package).get("protected_dirs", [])


def get_protected_root_files(package: str) -> List[str]:
    return pkg(package).get("protected_root_files", [])


def get_dockerfiles(package: str) -> List[Tuple[Version, Version, Path, dict]]:
    out: List[Tuple[Version, Version, Path, dict]] = []
    for entry in pkg(package).get("chains", []):
        lo, hi = entry["range"]
        build_args = {str(k): str(v) for k, v in (entry.get("build_args") or {}).items()}
        out.append((Version(str(lo)), Version(str(hi)), Path(entry["dockerfile"]), build_args))
    return out


def get_source(package: str) -> str:
    return pkg(package).get("source", default("source", "pypi"))


def get_github_repo(package: str) -> Tuple[str, str]:
    p = pkg(package)
    repo = p.get("repo")
    if not repo:
        raise ValueError(f"Package '{package}' has no 'repo' field in packages.yaml")
    return repo, str(p.get("tag_prefix", ""))


def get_excluded_versions(package: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in pkg(package).get("chains", []):
        for ex in entry.get("exclude_versions", []):
            out[str(ex["version"])] = ex.get("reason", "")
    return out
