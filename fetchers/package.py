import tarfile
import zipfile
import subprocess
import requests
from pathlib import Path
from typing import Literal, Optional, List
from urllib.parse import urlparse
from packaging.version import Version

PYPI_API = "https://pypi.org/pypi"

Source = Literal["pypi", "github"]

class PackageExtractor:
    def __init__(self, name: str) -> None:
        self.name = name
        self._package_info: Optional[dict] = None 
    
    
    @property
    def package_info(self) -> dict:
        if self._package_info is None:
            url = f"{PYPI_API}/{self.name}/json"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            self._package_info = response.json()
        return self._package_info
    
    @property
    def releases(self) -> dict:
        return self.package_info.get("releases", {})
    
    
    def get_available_versions(self, min_major: int = 1, min_minor: int = 0, sdist_only: bool = True) -> List[Version]:
        versions = []
        for version_str, files in self.releases.items():
            version = Version(version_str)
            if (
                version.is_prerelease
                or version.is_devrelease
                or version.major < min_major
                or (version.major == min_major and version.minor < min_minor)
                or (sdist_only and not any(f.get("packagetype") == "sdist" for f in files))
            ):
                continue
            versions.append(version)
        versions.sort()
        return versions
    
    
    def _get_source_url(self, version: Version) -> Optional[str]:
        key = str(version)
        if key in self.releases:
            files = self.releases[key]
            for file_info in files:
                if file_info.get("packagetype") == "sdist":
                    return file_info.get("url")
            for file_info in files:
                if file_info.get("packagetype") == "bdist_wheel":
                    return file_info.get("url")
        return None
    
    
    def download_from_pypi(self, version: Version, save_dir: str | Path) -> Path:
        package_dir = Path(save_dir) / self.name
        package_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if already downloaded and extracted
        version_path = package_dir / str(version)
        if version_path.exists():
            return version_path
        
        # Get source URL from PyPI and download the package
        url = self._get_source_url(version)
        if not url:
            raise ValueError(f"No source distribution found for version {version}")
        
        print(f"  Downloading {self.name} {version}...")
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()
        filename = Path(urlparse(url).path).name
        temp_file = package_dir / filename
        with open(temp_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Extract the downloaded archive
        top_dir = None
        if filename.endswith(".tar.gz") or filename.endswith(".tgz"):
            with tarfile.open(temp_file, "r:gz") as tar:
                tar.extractall(package_dir)
                members = tar.getmembers()
                if members:
                    first_path = members[0].name
                    top_dir = first_path.split("/")[0] if "/" in first_path else first_path
        elif filename.endswith(".zip"):
            with zipfile.ZipFile(temp_file, "r") as zip_ref:
                zip_ref.extractall(package_dir)
                names = zip_ref.namelist()
                if names:
                    first_path = names[0]
                    top_dir = first_path.split("/")[0] if "/" in first_path else first_path
        else:
            raise ValueError(f"Unsupported archive format: {filename}")
        
        temp_file.unlink()
        
        # Try to determine the extracted directory name if not provided by the archive
        if not top_dir:
            extracted_items = [
                item for item in package_dir.iterdir()
                if item.is_dir() and item.name != "__pycache__"
            ]
            if extracted_items:
                top_dir = extracted_items[0].name
            else:
                raise ValueError(f"Could not determine extracted directory for {version}")
        
        extracted_path = package_dir / top_dir
        if not extracted_path.exists():
            raise ValueError(f"Extracted directory not found: {extracted_path}")
        
        if not version_path.exists():
            extracted_path.rename(version_path)
            extracted_path = version_path
        return extracted_path
    
    
    def clone_from_github(self, version: Version, save_dir: str | Path) -> Path:
        from config import get_github_repo
        repo, tag_prefix = get_github_repo(self.name)
        tag = f"{tag_prefix}{version}"
        url = f"https://github.com/{repo}.git"
        package_dir = Path(save_dir) / self.name
        package_dir.mkdir(parents=True, exist_ok=True)
        
        version_dir = package_dir / str(version)
        if version_dir.exists():
            print(f"  Using cached {self.name} {version} from {version_dir}")
            return version_dir
        
        print(f"  Cloning {self.name} {version} (tag: {tag}) from GitHub...")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", tag, url, str(version_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        return version_dir
    
    
    def download(self, versions: List[Version | str], save_dir: str = ".packages", source: Source = "pypi") -> None:
        versions = [Version(v) if isinstance(v, str) else v for v in versions]
        for version in versions:
            if source == "pypi":
                self.download_from_pypi(version, save_dir)
            elif source == "github":
                self.clone_from_github(version, save_dir)
            else:
                raise ValueError(f"Unsupported source: {source}")
    