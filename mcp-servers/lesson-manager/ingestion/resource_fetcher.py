import requests
import json
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

SEARXNG_URL = "http://localhost:8080"

# Quality domain scoring
_BOOST_DOMAINS = {
    "docs.python.org": 3,
    "developer.mozilla.org": 3,
    "docs.microsoft.com": 3,
    "learn.microsoft.com": 3,
    "docs.aws.amazon.com": 3,
    "cloud.google.com": 2,
    "kubernetes.io": 2,
    "reactjs.org": 2,
    "vuejs.org": 2,
    "angular.io": 2,
    "nodejs.org": 2,
    "rust-lang.org": 2,
    "go.dev": 2,
    "djangoproject.com": 2,
    "flask.palletsprojects.com": 2,
    "fastapi.tiangolo.com": 2,
    "wikipedia.org": 1,
    "stackoverflow.com": 1,
    "github.com": 1,
    "realpython.com": 2,
    "w3schools.com": 1,
    "geeksforgeeks.org": 1,
    "medium.com": 0,
    "dev.to": 1,
    "freecodecamp.org": 2,
    "tutorial": 1,
}

_PENALIZE_DOMAINS = {
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    "tiktok.com",
    "yahoo.com",
    "answers.com",
}


class ResourceFetcher:
    def __init__(self, searxng_url: str = SEARXNG_URL):
        self.searxng_url = searxng_url

    def _search(self, query: str) -> List[Dict]:
        """Search SearXNG. Return raw results."""
        try:
            response = requests.post(
                f"{self.searxng_url}/search",
                data={"q": query, "format": "json", "categories": "general"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            logger.warning(f"SearXNG search failed for '{query}': {e}")
            return []

    def _classify_source(self, url: str, title: str) -> str:
        """Classify resource type: documentation | tutorial | official | article."""
        url_lower = url.lower()
        title_lower = title.lower()

        if any(d in url_lower for d in ["docs.", "/docs/", "documentation", "reference", "api/"]):
            return "documentation"
        if any(d in url_lower for d in ["docs.python.org", "developer.mozilla.org",
                                         "docs.microsoft.com", "learn.microsoft.com",
                                         "docs.aws.amazon.com"]):
            return "official"
        if any(k in title_lower for k in ["tutorial", "guide", "how to", "howto",
                                           "getting started", "introduction to"]):
            return "tutorial"
        if any(k in url_lower for k in ["tutorial", "guide", "howto", "getting-started"]):
            return "tutorial"
        return "article"

    def _score_result(self, result: Dict) -> float:
        """Compute quality score for a search result."""
        url = result.get("url", "").lower()
        title = result.get("title", "")
        score = 0.0

        # Penalize low-quality domains first
        for domain in _PENALIZE_DOMAINS:
            if domain in url:
                return -999.0

        # Boost high-quality domains
        for domain, boost in _BOOST_DOMAINS.items():
            if domain in url:
                score += boost
                break

        # Boost for keyword indicators in URL/title
        for kw in ("tutorial", "docs", "documentation", "guide", "reference", "official"):
            if kw in url or kw in title.lower():
                score += 0.5

        return score

    def _filter_quality(self, results: List[Dict]) -> List[Dict]:
        """Filter for quality sources and return top 5."""
        scored = []
        for r in results:
            s = self._score_result(r)
            if s > -999:
                scored.append((s, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:5]]

    def fetch_for_module(self, key_concepts: List[str], module_title: str) -> List[Dict]:
        """Fetch 3-5 quality resources for a module. Return list of {title, url, description, type}."""
        # Build 3 varied queries
        concepts_str = ", ".join(key_concepts[:3]) if key_concepts else module_title
        queries = [
            f"{module_title} tutorial",
            f"{concepts_str} documentation",
            f"{module_title} {key_concepts[0] if key_concepts else ''} guide".strip(),
        ]

        all_results = []
        seen_urls = set()

        for query in queries:
            raw = self._search(query)
            for r in raw:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)

        top = self._filter_quality(all_results)

        resources = []
        for r in top:
            url = r.get("url", "")
            title = r.get("title", "")
            resources.append({
                "title": title,
                "url": url,
                "description": r.get("content", r.get("snippet", "")),
                "type": self._classify_source(url, title),
            })

        return resources
