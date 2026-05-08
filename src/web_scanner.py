"""Scan a web page for casino ad content.

Fetches a URL, extracts visible text and outbound links, and returns
a list of records in the same shape as Meta Ad Library records so the
same classifier pipeline can process them.

One record is produced per page — the full visible text is treated as
ad copy and the page URL as the landing URL.
"""

from __future__ import annotations
import re
from urllib.parse import urlparse, urljoin


def _extract(url: str, timeout: float = 10.0) -> dict:
    """Fetch url and return {title, text, links, error}."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        return {"title": "", "text": "", "links": [], "error": str(e)}

    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"},
                         allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        return {"title": "", "text": "", "links": [], "error": str(e)}

    soup = BeautifulSoup(r.text, "html.parser")

    # Remove script / style noise
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # Meta description
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"].strip()

    # Visible text — collapse whitespace
    text = " ".join(soup.get_text(" ", strip=True).split())

    # Outbound links (external domains only)
    base_domain = urlparse(url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = urljoin(url, href)
        if href.startswith("http"):
            link_domain = urlparse(href).netloc
            if link_domain and link_domain != base_domain:
                links.append(href)

    # Deduplicate while preserving order
    seen = set()
    unique_links = [l for l in links if not (l in seen or seen.add(l))]

    combined_text = " | ".join(filter(None, [title, meta_desc]))

    return {
        "title": title,
        "text": combined_text or text[:1000],
        "links": unique_links[:20],
        "error": None,
    }


def scan_url(url: str) -> list[dict]:
    """Scan a URL and return a list of classifier-ready records.

    Returns one record for the page itself, plus one additional record
    per distinct outbound domain found (so affiliate hops are caught).
    """
    data = _extract(url)
    if data["error"]:
        return [{
            "id": url,
            "page_name": url,
            "ad_creative_bodies": [],
            "ad_creative_link_captions": [url],
            "_resolved_link": url,
            "_scan_error": data["error"],
        }]

    records = []

    # Primary record — the page itself
    records.append({
        "id": url,
        "page_name": data["title"] or url,
        "ad_creative_bodies": [data["text"]] if data["text"] else [],
        "ad_creative_link_captions": [url],
        "_resolved_link": url,
        "_source": "web_scan",
    })

    # One record per unique outbound link found on the page
    seen_domains: set[str] = set()
    for link in data["links"]:
        domain = urlparse(link).netloc
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        records.append({
            "id": link,
            "page_name": f"{data['title'] or url} → {domain}",
            "ad_creative_bodies": [data["text"]] if data["text"] else [],
            "ad_creative_link_captions": [link],
            "_resolved_link": link,
            "_source": "web_scan_link",
        })

    return records
