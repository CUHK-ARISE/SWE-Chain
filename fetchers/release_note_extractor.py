import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from .github_fetcher import GitHubLink

GITHUB_LINK_PATTERN = re.compile(r'https://github\.com/[^/]+/[^/]+/(?:issues|pull)/\d+')


def extract_github_links(text: str) -> List[str]:
    return GITHUB_LINK_PATTERN.findall(text)


def create_github_link(url: str) -> Optional[GitHubLink]:
    try:
        return GitHubLink(url)
    except Exception:
        return None


def extract_release_note_items(release_note_path: str, output_file: str = None, workers: int = 8) -> None:
    
    release_note_path = Path(release_note_path)
    if not release_note_path.exists():
        raise FileNotFoundError(f"Release note file not found: {release_note_path}")
    
    with open(release_note_path, "r", encoding="utf-8") as f:
        md_content = f.read()
    
    lines = md_content.split("\n")
    
    task_id = 1
    links = set()
    tasks_raw = list()
    current_task = None
    
    for line in lines:
        if re.match(r'^- ', line):
            if current_task:
                tasks_raw.append(current_task)
            content = line[2:].strip()
            current_task = {
                "task_id": f"task_{task_id}",
                "content": content,
                "github_links": []
            }
            github_links = extract_github_links(content)
            if github_links:
                current_task["github_links"].extend(github_links)
                links.update(github_links)
            task_id += 1
        elif re.match(r'^\s+- ', line):     # Sub-item of the current task
            if current_task:
                current_task["content"] += "\n" + line.strip()
                github_links = extract_github_links(line)
                if github_links:
                    current_task["github_links"].extend(github_links)
                    links.update(github_links)
    
    if current_task:
        tasks_raw.append(current_task)
    
    # Parallel fetch all GitHub links using GitHubLink instances
    link_cache: Dict[str, GitHubLink] = {}
    
    if links:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_link = {
                executor.submit(create_github_link, link): link
                for link in links
            }
            for future in as_completed(future_to_link):
                original_link = future_to_link[future]
                gh_link = future.result()
                if gh_link:
                    link_cache[original_link] = gh_link
    
    # Second pass: build final tasks with fetched content
    tasks = []
    for task_raw in tasks_raw:
        task = {
            "task_id": task_raw["task_id"],
            "content": task_raw["content"]
        }
        if task_raw["github_links"]:
            task["github"] = []
            for link in task_raw["github_links"]:
                gh_link = link_cache.get(link)
                if gh_link:
                    task["github"].append({
                        "link": gh_link.url,
                        "type": gh_link.gh_type,
                        "content": gh_link.get_gh_content()
                    })
        tasks.append(task)
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)
    print(f"  Converted {len(tasks)} tasks to {output_file}")
