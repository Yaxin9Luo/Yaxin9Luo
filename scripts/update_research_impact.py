#!/usr/bin/env python3
"""Manage the GitHub profile README research impact section.

Usage:
  python scripts/update_research_impact.py update
  python scripts/update_research_impact.py add owner/repo --focus "Short description"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


START_MARKER = "<!-- RESEARCH-IMPACT:START -->"
END_MARKER = "<!-- RESEARCH-IMPACT:END -->"
DEFAULT_REPO_LIST = Path("data/research-repos.json")
DEFAULT_README = Path("README.md")


def normalize_repo(repo: str) -> str:
    repo = repo.strip()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if repo.startswith(prefix):
            repo = repo[len(prefix) :]
            break
    if repo.endswith(".git"):
        repo = repo[:-4]
    repo = repo.strip("/")

    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected repository as owner/repo, got: {repo!r}")
    if any(part.startswith(".") or part.endswith(".") for part in parts):
        raise ValueError(f"Invalid repository name: {repo!r}")
    return repo


def load_repo_list(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    repos = config.get("repos")
    if not isinstance(repos, list):
        raise ValueError(f"{path} must contain a top-level 'repos' list")

    seen: set[str] = set()
    for item in repos:
        if not isinstance(item, dict):
            raise ValueError("Each repo entry must be an object")
        item["repo"] = normalize_repo(str(item.get("repo", "")))
        if item["repo"].lower() in seen:
            raise ValueError(f"Duplicate repo entry: {item['repo']}")
        seen.add(item["repo"].lower())
    return config


def save_repo_list(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_repo(repo: str, token: str | None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Yaxin9Luo-profile-impact-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API failed for {repo}: HTTP {exc.code} {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API failed for {repo}: {exc.reason}") from exc


def escape_markdown_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def build_entry(item: dict[str, Any], token: str | None, offline: bool) -> dict[str, Any]:
    repo = item["repo"]
    if offline:
        stars = int(item.get("fallback_stars", 0))
        forks = int(item.get("fallback_forks", 0))
        url = f"https://github.com/{repo}"
        description = item.get("focus") or "Research code repository."
        full_name = repo
    else:
        api_data = fetch_repo(repo, token)
        stars = int(api_data.get("stargazers_count", 0))
        forks = int(api_data.get("forks_count", 0))
        url = str(api_data.get("html_url") or f"https://github.com/{repo}")
        description = str(api_data.get("description") or "")
        full_name = str(api_data.get("full_name") or repo)

    focus = str(item.get("focus") or description or "Research code repository.")
    return {
        "repo": repo,
        "full_name": full_name,
        "url": url,
        "stars": stars,
        "forks": forks,
        "focus": focus,
    }


def build_markdown(entries: list[dict[str, Any]], fetched_at: str) -> str:
    total_stars = sum(entry["stars"] for entry in entries)
    total_forks = sum(entry["forks"] for entry in entries)
    repo_count = len(entries)

    stars_badge = quote(str(total_stars), safe="")
    forks_badge = quote(str(total_forks), safe="")
    repos_badge = quote(str(repo_count), safe="")

    lines = [
        '<p align="center">',
        (
            '  <img src="https://img.shields.io/badge/research%20code%20stars-'
            f'{stars_badge}-00D4FF?style=for-the-badge&labelColor=0B1221" '
            'alt="Research code stars"/>'
        ),
        (
            '  <img src="https://img.shields.io/badge/forks-'
            f'{forks_badge}-7C3AED?style=for-the-badge&labelColor=0B1221" '
            'alt="Research code forks"/>'
        ),
        (
            '  <img src="https://img.shields.io/badge/tracked%20repos-'
            f'{repos_badge}-10B981?style=for-the-badge&labelColor=0B1221" '
            'alt="Tracked repositories"/>'
        ),
        "</p>",
        "",
        (
            f"Selected projects I lead or contribute to have received "
            f"**{total_stars:,} GitHub stars** and **{total_forks:,} forks** "
            f"across **{repo_count}** personal and organization repositories."
        ),
        "",
        "<details>",
        "<summary><b>Tracked repositories</b></summary>",
        "",
        "| Repository | Stars | Forks | Focus |",
        "| --- | ---: | ---: | --- |",
    ]

    for entry in sorted(entries, key=lambda value: (-value["stars"], value["full_name"].lower())):
        repo_link = f"[{escape_markdown_table(entry['full_name'])}]({entry['url']})"
        focus = escape_markdown_table(entry["focus"])
        lines.append(f"| {repo_link} | {entry['stars']:,} | {entry['forks']:,} | {focus} |")

    lines.extend(
        [
            "",
            "</details>",
            "",
            (
                f"<sub>Last updated: {fetched_at}. "
                f"Managed from [data/research-repos.json](data/research-repos.json).</sub>"
            ),
        ]
    )
    return "\n".join(lines)


def replace_impact_block(readme: Path, block: str) -> None:
    content = readme.read_text(encoding="utf-8")
    pattern = re.compile(f"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
    replacement = f"{START_MARKER}\n{block}\n{END_MARKER}"
    updated, count = pattern.subn(replacement, content)
    if count != 1:
        raise RuntimeError(f"Expected exactly one research impact block in {readme}")
    readme.write_text(updated, encoding="utf-8")


def update_command(args: argparse.Namespace) -> int:
    config = load_repo_list(args.repo_list)
    token = args.token or os.environ.get("GITHUB_TOKEN")
    fetched_at = args.fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entries = [build_entry(item, token, args.offline) for item in config["repos"]]
    block = build_markdown(entries, fetched_at)
    if args.dry_run:
        print(block)
        return 0

    replace_impact_block(args.readme, block)
    return 0


def add_command(args: argparse.Namespace) -> int:
    repo = normalize_repo(args.repo)
    if args.repo_list.exists():
        config = load_repo_list(args.repo_list)
    else:
        config = {"repos": []}

    for item in config["repos"]:
        if item["repo"].lower() == repo.lower():
            if args.focus:
                item["focus"] = args.focus.strip()
                save_repo_list(args.repo_list, config)
                print(f"Updated focus for {repo}")
            else:
                print(f"{repo} is already in {args.repo_list}")
            return 0

    entry: dict[str, Any] = {"repo": repo}
    if args.focus:
        entry["focus"] = args.focus.strip()
    config["repos"].append(entry)
    save_repo_list(args.repo_list, config)
    print(f"Added {repo} to {args.repo_list}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser("update", help="Refresh the README impact block")
    update.add_argument("--repo-list", type=Path, default=DEFAULT_REPO_LIST)
    update.add_argument("--readme", type=Path, default=DEFAULT_README)
    update.add_argument("--token", default=None, help="GitHub token; defaults to GITHUB_TOKEN")
    update.add_argument("--offline", action="store_true", help="Use fallback stats from the repo list")
    update.add_argument("--fetched-at", default=None, help="Override the displayed update date")
    update.add_argument("--dry-run", action="store_true", help="Print generated markdown without editing")
    update.set_defaults(func=update_command)

    add = subparsers.add_parser("add", help="Add a repository to the impact repo list")
    add.add_argument("repo", help="Repository in owner/repo form or a GitHub repository URL")
    add.add_argument("--repo-list", type=Path, default=DEFAULT_REPO_LIST)
    add.add_argument("--focus", default="", help="Optional one-line focus shown in the README table")
    add.set_defaults(func=add_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
