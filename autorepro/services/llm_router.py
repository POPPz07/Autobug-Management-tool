"""
AutoRepro Enterprise — LLM Router Service
services/llm_router.py

Selects the appropriate LLM provider and model based on DOM complexity.

Heuristic (element count AND raw HTML size from a lightweight pre-flight fetch):
  element_count < 50  AND html_size < 50 KB  → ollama  (local, cheap, fast)
  element_count < 200                         → gemini  (cloud, mid-cost, good quality)
  else                                        → bedrock (cloud, high-cost, complex pages)

The dual condition for the ollama tier ensures we don't route a tiny-element-count
but data-heavy page to the local model (which has a small context window).

IMPORTANT: This module does NOT call agent/inspect_node. It performs an
independent lightweight HTTP + BeautifulSoup parse ONLY to count interactive
elements and measure page size. The agent still runs its own full inspect.

This keeps services/ fully independent of agent/ and sandbox/.

Callers:
  - services/job_trigger.py → select_llm(target_url) → LLMSelection
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from utils.config import (
    PRIMARY_LLM_PROVIDER, PRIMARY_LLM_MODEL,
    FALLBACK_LLM_PROVIDER, FALLBACK_LLM_MODEL,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Routing thresholds ─────────────────────────────────────────────
_OLLAMA_ELEMENT_CEILING = 50       # elements
_OLLAMA_SIZE_CEILING    = 50_000   # bytes (50 KB); both must hold for ollama
_GEMINI_ELEMENT_CEILING = 200      # elements; above this → bedrock

# ── Bedrock model to use for complex pages ─────────────────────────
_BEDROCK_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"

# ── Tags counted as "interactive elements" (mirrors inspect_node) ──
_INTERACTIVE_TAGS = (
    "a", "button", "input", "select", "textarea",
    "form", "label", "nav", "h1", "h2", "h3",
)

# ── Timeout for the pre-flight fetch ──────────────────────────────
_FETCH_TIMEOUT = 10   # seconds


@dataclass(frozen=True)
class LLMSelection:
    """
    Result of LLM routing decision.

    Attributes:
        provider:      e.g. "ollama", "gemini", "bedrock"
        model:         e.g. "qwen2.5-coder:3b", "gemini-2.0-flash"
        element_count: number of interactive elements found (or -1 on fetch failure)
        html_size:     raw HTML byte count (or -1 on fetch failure)
        source:        "heuristic" | "fallback" (config default on fetch failure)
    """
    provider:      str
    model:         str
    element_count: int
    html_size:     int   # bytes; -1 if fetch failed
    source:        str   # "heuristic" or "fallback"

    @property
    def llm_used(self) -> str:
        """Canonical string stored in Job.llm_used."""
        return f"{self.provider}/{self.model}"


def _normalize_url(url: str) -> str:
    """Translate Docker-internal hostnames to localhost for pre-flight fetch."""
    return re.sub(r"host\.docker\.internal", "localhost", url, flags=re.IGNORECASE)


def _fetch_page_stats(url: str) -> Optional[tuple[int, int]]:
    """
    Do a lightweight GET + parse.

    Returns:
        (element_count, html_size_bytes) on success.
        None on any fetch or parse failure.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AutoRepro-Router/1.0)"}
        resp = requests.get(
            _normalize_url(url),
            headers=headers,
            timeout=_FETCH_TIMEOUT,
            verify=False,
        )
        resp.raise_for_status()
        html_bytes    = resp.content          # raw bytes for accurate size
        html_size     = len(html_bytes)
        soup          = BeautifulSoup(resp.text, "html.parser")
        element_count = len(soup.find_all(_INTERACTIVE_TAGS))
        return element_count, html_size
    except Exception as exc:
        log.warning("llm_router_fetch_failed", url=url, error=str(exc))
        return None


def _select_by_stats(element_count: int, html_size: int) -> tuple[str, str]:
    """
    Pure routing heuristic — no network calls.

    Rules (in priority order):
      1. Small page (few elements AND small HTML) → ollama (local, cheap)
      2. Medium page (moderate elements)          → gemini  (cloud mid-tier)
      3. Large/complex page                       → bedrock (cloud premium)

    Returns (provider, model).
    """
    if element_count < _OLLAMA_ELEMENT_CEILING and html_size < _OLLAMA_SIZE_CEILING:
        return "ollama", PRIMARY_LLM_MODEL

    if element_count < _GEMINI_ELEMENT_CEILING:
        return FALLBACK_LLM_PROVIDER, FALLBACK_LLM_MODEL

    return "bedrock", _BEDROCK_MODEL


def select_llm(target_url: str) -> LLMSelection:
    """
    Determine the best LLM for this job based on page complexity.

    Steps:
      1. Fetch the target URL (lightweight, no JS execution).
      2. Count interactive elements and measure raw HTML size.
      3. Route to provider based on dual threshold (element_count + html_size).
      4. On any failure: fall back silently to PRIMARY_LLM config.

    Returns:
        LLMSelection with provider, model, element_count, html_size, source.
    """
    stats = _fetch_page_stats(target_url)

    if stats is None:
        log.info(
            "llm_router_fallback",
            url      = target_url,
            reason   = "fetch_failed",
            fallback = f"{PRIMARY_LLM_PROVIDER}/{PRIMARY_LLM_MODEL}",
        )
        return LLMSelection(
            provider      = PRIMARY_LLM_PROVIDER,
            model         = PRIMARY_LLM_MODEL,
            element_count = -1,
            html_size     = -1,
            source        = "fallback",
        )

    element_count, html_size = stats
    provider, model = _select_by_stats(element_count, html_size)

    log.info(
        "llm_router_selected",
        url           = target_url,
        element_count = element_count,
        html_size_kb  = round(html_size / 1024, 1),
        provider      = provider,
        model         = model,
    )
    return LLMSelection(
        provider      = provider,
        model         = model,
        element_count = element_count,
        html_size     = html_size,
        source        = "heuristic",
    )
