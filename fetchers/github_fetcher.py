import re
import json
import subprocess
from typing import Dict, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


GITHUB_ISSUE_TEMPLATE = """
### GitHub Issue #{number} {title}
{content}
"""

GITHUB_PR_TEMPLATE = """
### GitHub Pull Request #{number} {title}
{content}
"""


class GitHubLink(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    url: str
    gh_type: Literal["issue", "pull"] = Field(default="")
    owner: str = Field(default="")
    repo: str = Field(default="")
    number: str = Field(default="")
    data: Optional[Dict] = Field(default=None)
    
    def __init__(self, url: str):
        super().__init__(url=url)
        self._parse_url()
        self._fetch_data()
    
    def _parse_url(self):
        """Parse the GitHub URL to extract owner, repo, number, and type (issue or PR)."""
        if "/issues/" in self.url:
            self.gh_type = "issue"
        elif "/pull/" in self.url:
            self.gh_type = "pr"
        else:
            raise ValueError(f"Invalid GitHub URL: {self.url}")
        parts = self.url.rstrip('/').split('/')
        self.owner = parts[-4]
        self.repo = parts[-3]
        self.number = parts[-1]
    
    def _fetch_data(self) -> bool:
        """Fetch data from GitHub using the GitHub CLI."""
        cmd = [
            "gh", self.gh_type, "view", self.number,
            "--repo", f"{self.owner}/{self.repo}",
            "--json", "title,body,number,url"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Try other type (Issue <-> PR) if the first attempt fails, as some URLs might be misclassified
        if result.returncode != 0:
            alt_type = "issue" if self.gh_type == "pr" else "pr"
            alt_cmd = [
                "gh", alt_type, "view", self.number,
                "--repo", f"{self.owner}/{self.repo}",
                "--json", "title,body,number,url"
            ]
            result = subprocess.run(alt_cmd, capture_output=True, text=True)
            
            # If the alternate type also fails, raise an error. Otherwise, update the type and continue.
            if result.returncode == 0:
                self.gh_type = alt_type
            else:
                print(f"    Error fetching data from GitHub CLI: {result.stderr.strip()}")
                raise RuntimeError(f"Failed to fetch data from GitHub CLI: {result.stderr.strip()}")
        
        # Clean the ANSI escape codes if present and parse JSON
        try:
            self.data = json.loads(result.stdout)
        except json.JSONDecodeError:
            cleaned = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout.strip())
            self.data = json.loads(cleaned)
        
        # Handle case where type was wrong and data is empty
        if self.data and "url" in self.data:
            actual_type = "pr" if "/pull/" in self.data["url"] else "issue"
            if actual_type != self.gh_type:
                self.gh_type = actual_type
            self.url = self.data["url"]
    
    def get_gh_content(self) -> Optional[str]:
        if not self.data:
            return None
        template = GITHUB_ISSUE_TEMPLATE if self.gh_type == "issue" else GITHUB_PR_TEMPLATE
        return template.format(
            number=self.number,
            title=self.data["title"],
            content=self.data["body"]
        ).strip()
