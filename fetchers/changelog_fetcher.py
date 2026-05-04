import re
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from packaging.version import Version
from fetchers.page_parser import covert_html_to_md


def _fetch_changelog_by_section(url: str, versions: List[Version], version_prefix: str = "") -> Dict[Version, Any]:
    html = requests.get(url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    all_h2 = soup.find_all("h2")
    result = {}
    for v in versions:
        candidates = [f"{version_prefix}{v}"]
        if v.micro == 0:
            candidates.append(f"{version_prefix}{v.major}.{v.minor}")
        matched = None
        for h2 in all_h2:
            title = h2.get_text(" ", strip=True)
            for candidate in candidates:
                pattern = rf"^{re.escape(candidate)}(?=[^\d.]|$)"
                if re.search(pattern, title):
                    parent_section = h2.find_parent("section")
                    section_id = parent_section.get("id") if parent_section else None
                    matched = {
                        "section_id": section_id,
                        "title": title,
                        "html": str(parent_section) if parent_section else "",
                        "content": covert_html_to_md(str(parent_section)) if parent_section else "",
                    }
                    break
            if matched:
                break
        result[v] = matched
    return result


def fetch_pyjwt_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://pyjwt.readthedocs.io/en/stable/changelog.html", versions, version_prefix="v")


def fetch_flask_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://flask.palletsprojects.com/en/stable/changes/", versions, version_prefix="Version ")


def fetch_pytest_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://docs.pytest.org/en/stable/changelog.html", versions, version_prefix="pytest ")


def fetch_conan_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://docs.conan.io/2/changelog.html", versions)


def fetch_attrs_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://www.attrs.org/en/stable/changelog.html", versions)


def fetch_jinja2_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://jinja.palletsprojects.com/en/stable/changes/", versions, version_prefix="Version ")


def fetch_urllib3_changelog(versions: List[Version]) -> Dict[Version, Any]:
    return _fetch_changelog_by_section("https://urllib3.readthedocs.io/en/stable/changelog.html", versions, version_prefix="")


def fetch_xarray_changelog(versions: List[Version]) -> Dict[Version, Any]:
    base_url = "https://docs.xarray.dev/en/stable/whats-new.html"
    html = requests.get(base_url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    normalized_versions = []
    for v in versions:
        s = str(v)
        parts = s.split(".")
        normalized_versions.append(f"{parts[0]}.{int(parts[1]):02d}.{parts[2]}")
    result = {}
    for idx, nversion in enumerate(normalized_versions):
        matched = None
        for h2 in soup.find_all("h2"):
            title = h2.get_text(" ", strip=True)
            if nversion in title:
                parent_section = h2.find_parent("section")
                section_id = parent_section.get("id") if parent_section else None
                matched = {
                    "section_id": section_id,
                    "title": title,
                    "html": str(parent_section) if parent_section else "",
                    "content": covert_html_to_md(str(parent_section)) if parent_section else "",
                }
                break
        result[versions[idx]] = matched
    return result


def fetch_poetry_changelog(versions: List[Version]) -> Dict[Version, Any]:
    content = (Path(__file__).parent / "poetry_changelog.md").read_text()
    sections = [(m.group(1), m.end()) for m in re.finditer(r"^(## .+)$", content, re.MULTILINE)]
    parsed_sections = []
    for i, (title, end) in enumerate(sections):
        body_end = sections[i + 1][1] - len(sections[i + 1][0]) if i + 1 < len(sections) else len(content)
        parsed_sections.append((title, content[end:body_end].strip()))
    version_title_re = re.compile(r"^\#\#\s+\[([^\]]+)\]")
    result: Dict[Version, Optional[Dict[str, Any]]] = {}
    for v in versions:
        candidates = [str(v)]
        if v.micro == 0:
            candidates.append(f"{v.major}.{v.minor}")
        matched = None
        for title, body in parsed_sections:
            m = version_title_re.match(title)
            if not m:
                continue
            title_version = m.group(1).strip()
            if title_version in candidates:
                matched = {"title": title, "version": title_version, "content": body}
                break
        result[v] = matched
    return result


FETCHER_MAP = {
    "flask": fetch_flask_changelog,
    "pytest": fetch_pytest_changelog,
    "xarray": fetch_xarray_changelog,
    "pyjwt": fetch_pyjwt_changelog,
    "conan": fetch_conan_changelog,
    "attrs": fetch_attrs_changelog,
    "jinja2": fetch_jinja2_changelog,
    "poetry": fetch_poetry_changelog,
    "urllib3": fetch_urllib3_changelog,
}
