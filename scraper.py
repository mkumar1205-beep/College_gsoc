#!/usr/bin/env python3
"""GSoC contributor enrichment script.

Fetches contributor names from GSoC archive pages and tries to find
university/college hints from GitHub profiles (and optionally web search
results for LinkedIn/Bing snippets).
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass
from typing import Iterable

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; gsoc-college-scraper/0.1; +https://github.com/)"


@dataclass
class Contributor:
    name: str
    project_title: str
    project_url: str


@dataclass
class EnrichedContributor(Contributor):
    github_url: str | None = None
    college: str | None = None
    source: str | None = None


def fetch_projects(year: int, limit: int | None = None) -> list[Contributor]:
    """Parse GSoC archive project cards for a given year.

    Note: HTML structure can change; keep selectors easy to update.
    """
    url = f"https://summerofcode.withgoogle.com/archive/{2026}/projects/"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select("a[href*='/archive/'][href*='/projects/']")

    seen: set[str] = set()
    rows: list[Contributor] = []

    for a in cards:
        href = a.get("href")
        if not href or href in seen:
            continue
        seen.add(href)

        title_el = a.select_one("h4, h3")
        person_el = a.select_one("p, span")
        title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
        person_text = (person_el.get_text(" ", strip=True) if person_el else "").strip()
        if not title or not person_text:
            continue

        name = person_text.split(" at ")[0].strip(" -")
        project_url = f"https://summerofcode.withgoogle.com{href}" if href.startswith("/") else href
        rows.append(Contributor(name=name, project_title=title, project_url=project_url))

        if limit and len(rows) >= limit:
            break

    return rows


def search_github_profile(name: str) -> str | None:
    q = f"{name} github"
    url = "https://duckduckgo.com/html/"
    resp = requests.post(url, data={"q": q}, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if "github.com/" in href:
            m = re.search(r"https?://github\.com/([A-Za-z0-9-]+)", href)
            if m:
                return f"https://github.com/{m.group(1)}"
    return None


def extract_college_from_github(profile_url: str) -> str | None:
    resp = requests.get(profile_url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    bio_bits = []
    for sel in [".p-note", "li[itemprop='worksFor']", "li[itemprop='homeLocation']", ".vcard-details li"]:
        for el in soup.select(sel):
            txt = el.get_text(" ", strip=True)
            if txt:
                bio_bits.append(txt)

    text = " | ".join(bio_bits)
    uni_patterns = [
        r"([A-Z][A-Za-z&\-\s]+University)",
        r"([A-Z][A-Za-z&\-\s]+Institute of Technology)",
        r"([A-Z][A-Za-z&\-\s]+College)",
    ]
    for pat in uni_patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


def enrich(contributors: Iterable[Contributor], delay: float = 1.2) -> list[EnrichedContributor]:
    out: list[EnrichedContributor] = []
    for c in contributors:
        github = search_github_profile(c.name)
        college = None
        source = None
        if github:
            college = extract_college_from_github(github)
            if college:
                source = "github"

        out.append(
            EnrichedContributor(
                name=c.name,
                project_title=c.project_title,
                project_url=c.project_url,
                github_url=github,
                college=college,
                source=source,
            )
        )
        time.sleep(delay)
    return out


def write_csv(rows: Iterable[EnrichedContributor], output: str) -> None:
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "project_title", "project_url", "github_url", "college", "source"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r.__dict__)


def main() -> None:
    p = argparse.ArgumentParser(description="Extract GSoC contributors and infer colleges via GitHub profiles.")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--output", default="contributors_enriched.csv")
    args = p.parse_args()

    contribs = fetch_projects(args.year, args.limit)
    enriched = enrich(contribs)
    write_csv(enriched, args.output)
    print(f"Saved {len(enriched)} rows to {args.output}")


if __name__ == "__main__":
    main()
