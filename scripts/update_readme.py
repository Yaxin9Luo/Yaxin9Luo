#!/usr/bin/env python3
"""Refresh the generated parts of the profile README.

Two marker blocks are rewritten in place:
  <!-- RESEARCH-IMPACT:START --> ... <!-- RESEARCH-IMPACT:END -->   featured work, with live GitHub stars
  <!-- PUBLICATIONS:START --> ... <!-- PUBLICATIONS:END -->         all publications (from the homepage data)

Fails safely: on any error (network, bad data, missing markers) README.md is left untouched
and the script exits non-zero, so the workflow commits nothing.

Usage:
  python scripts/update_readme.py            # GitHub API for stars, homepage data for publications
  python scripts/update_readme.py --offline  # no network: fallback stars, publications from data file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DATA = ROOT / "data" / "research-repos.json"
UA = "Yaxin9Luo-profile-readme-updater"


def get_json(url: str, token: str | None = None) -> object:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:
        return json.load(resp)


def fetch_stars(featured: list[dict], token: str | None) -> dict[str, int]:
    stars = {}
    for item in featured:
        data = get_json(f"https://api.github.com/repos/{item['repo']}", token)
        count = data.get("stargazers_count") if isinstance(data, dict) else None
        if not isinstance(count, int):
            raise ValueError(f"no stargazers_count for {item['repo']}")
        stars[item["repo"]] = count
    return stars


def render_featured(featured: list[dict], stars: dict[str, int]) -> str:
    lines: list[str] = []
    phase = None
    for item in featured:
        if item["phase"] != phase:
            phase = item["phase"]
            lines += (["", f"**{phase}**", ""] if lines else [f"**{phase}**", ""])
        n = stars[item["repo"]]
        lines.append(
            f"- [**{item['name']}**](https://github.com/{item['repo']}) ★ {n:,} · "
            f"{item['venue']} · *{item['role']}*<br>{item['focus']}"
        )
    return "\n".join(lines)


def _title(r: dict) -> str | None:
    t = r.get("title")
    return t.get("en") if isinstance(t, dict) else t


def _link(r: dict) -> str | None:
    links = r.get("links") or []
    return links[0].get("url") if links and isinstance(links[0], dict) else None


def merge_publications(entries: list[dict], portfolio: object) -> list[dict]:
    """Roles come from the data file; titles, venues and paper links from the homepage when available."""
    remote_list = portfolio.get("publications", []) if isinstance(portfolio, dict) else []
    remote = {p["id"]: p for p in remote_list if isinstance(p, dict) and "id" in p}
    known, out = set(), []
    for e in entries:
        known.add(e["id"])
        p = dict(e)
        r = remote.get(e["id"])
        if r:
            p.update({k: v for k, v in (("title", _title(r)), ("venue", r.get("venue")),
                                        ("year", r.get("year")), ("url", _link(r))) if v})
        if not (p.get("title") and p.get("venue") and p.get("year")):
            raise ValueError(f"publication {e['id']} is missing title, venue or year")
        out.append(p)
    for pid, r in remote.items():  # new on the homepage, not yet in the data file
        if pid in known or not (_title(r) and r.get("venue") and r.get("year")):
            continue
        authors = str(r.get("authors", ""))
        role = ("first author" if authors.startswith("Yaxin Luo")
                else "co-first author" if "Yaxin Luo*" in authors else "co-author")
        out.append({"id": pid, "role": role, "title": _title(r), "venue": r["venue"],
                    "year": r["year"], "url": _link(r)})
    out.sort(key=lambda p: -int(p["year"]))  # stable: keeps data-file order within a year
    return out


def render_publications(pubs: list[dict], page: str) -> str:
    lines = [f"<details>\n<summary><b>All publications</b> ({len(pubs)})</summary>\n"]
    for p in pubs:
        title = f"[{p['title']}]({p['url']})" if p.get("url") else p["title"]
        lines.append(f"- **{p['venue']} {p['year']}** · {title} · *{p['role']}*")
    lines.append(f"\nAbstracts, figures and co-authors on the [homepage]({page}).\n</details>")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.find(end) < text.find(start):
        raise ValueError(f"markers for {name} missing or duplicated")
    i, j = text.find(start), text.find(end)
    return text[: i + len(start)] + "\n" + body + "\n" + text[j:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="no network; fallback stars, data-file publications")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if README would change")
    args = ap.parse_args()
    try:
        config = json.loads(DATA.read_text(encoding="utf-8"))
        featured = config["featured"]
        if args.offline:
            stars = {f["repo"]: int(f["fallback_stars"]) for f in featured}
            portfolio = None
        else:
            stars = fetch_stars(featured, os.environ.get("GITHUB_TOKEN"))
            try:
                portfolio = get_json(config["portfolio_url"])
            except Exception as exc:  # optional source: fall back to the data file
                print(f"warning: homepage data unavailable ({exc}); using data file", file=sys.stderr)
                portfolio = None
        pubs = merge_publications(config["publications"], portfolio)
        old = README.read_text(encoding="utf-8")
        new = replace_block(old, "RESEARCH-IMPACT", render_featured(featured, stars))
        new = replace_block(new, "PUBLICATIONS", render_publications(pubs, config["publications_page"]))
    except Exception as exc:
        print(f"error: {exc}; README left unchanged", file=sys.stderr)
        return 1
    if new == old:
        print("README already up to date")
        return 0
    if args.check:
        print("README would change")
        return 1
    if not args.offline:  # keep fallbacks current so an offline run never writes stale numbers
        for f in featured:
            f["fallback_stars"] = stars[f["repo"]]
        DATA.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    README.write_text(new, encoding="utf-8")
    print("README updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
