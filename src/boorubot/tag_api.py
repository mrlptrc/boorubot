"""Autocomplete e conferência de tags pelo endpoint público do Danbooru."""

from __future__ import annotations

import time
from collections.abc import Iterable

import httpx

from boorubot.sites.danbooru import USER_AGENT

BASE_URL = "https://danbooru.donmai.us"
CACHE_SECONDS = 600


class DanbooruTagAPI:
    """Cache curto em memória para não consultar o Danbooru a cada tecla."""

    def __init__(self, transport: httpx.BaseTransport | None = None, ttl: int = CACHE_SECONDS):
        self.transport = transport
        self.ttl = ttl
        self.autocomplete_cache: dict[str, tuple[float, list[dict]]] = {}
        self.exists_cache: dict[str, tuple[float, bool]] = {}

    def _get(self, path: str, params, login: str, api_key: str):
        auth = (login, api_key) if login and api_key else None
        with httpx.Client(base_url=BASE_URL, auth=auth, transport=self.transport, timeout=8,
                          headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _fresh(entry, now: float):
        return entry is not None and entry[0] > now

    def autocomplete(self, query: str, login: str = "", api_key: str = "") -> list[dict]:
        query = query.strip().lower()[:80]
        if len(query) < 2:
            return []
        now = time.monotonic()
        cached = self.autocomplete_cache.get(query)
        if self._fresh(cached, now):
            return cached[1]
        result = self._get("/autocomplete.json", {"search[type]": "tag_query", "search[query]": query},
                           login, api_key)
        suggestions = []
        for item in result if isinstance(result, list) else []:
            if isinstance(item, str):
                name = item
                extra = {}
            elif isinstance(item, dict):
                name = item.get("value") or item.get("name") or item.get("label") or ""
                extra = {k: item[k] for k in ("category", "post_count") if k in item}
            else:
                continue
            if name:
                suggestions.append({"value": str(name), **extra})
        suggestions = suggestions[:10]
        self.autocomplete_cache[query] = (now + self.ttl, suggestions)
        self._trim(self.autocomplete_cache)
        return suggestions

    def unknown(self, tags: Iterable[str], login: str = "", api_key: str = "") -> list[str]:
        names = list(dict.fromkeys(str(tag).strip().lower() for tag in tags if str(tag).strip()))[:200]
        now = time.monotonic()
        missing = [tag for tag in names if not self._fresh(self.exists_cache.get(tag), now)]
        for start in range(0, len(missing), 100):
            batch = missing[start:start + 100]
            params = [("search[name_array][]", tag) for tag in batch]
            params.extend((("search[hide_empty]", "false"), ("limit", "100")))
            result = self._get("/tags.json", params, login, api_key)
            found = {str(row.get("name", "")).lower() for row in result if isinstance(row, dict)} \
                if isinstance(result, list) else set()
            saved_at = time.monotonic() + self.ttl
            for tag in batch:
                self.exists_cache[tag] = (saved_at, tag in found)
        self._trim(self.exists_cache)
        return [tag for tag in names if not self.exists_cache[tag][1]]

    @staticmethod
    def _trim(cache: dict, max_entries: int = 256) -> None:
        if len(cache) <= max_entries:
            return
        for key in list(cache)[:len(cache) - max_entries]:
            del cache[key]
