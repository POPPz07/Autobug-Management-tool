"""Node 1.5 — DOM inspection: fetch target page and extract interactive elements."""

import re
import requests
from bs4 import BeautifulSoup

from agent.state import AgentState
from utils.logger import get_logger

log = get_logger(__name__)


def _host_url(url: str) -> str:
    """Translate Docker-internal URLs to host-reachable equivalents.

    The inspect node runs on the host machine (Windows/macOS), where
    'host.docker.internal' doesn't resolve. We swap it to 'localhost'
    so requests.get() can actually reach the target app.
    """
    return re.sub(
        r'host\.docker\.internal',
        'localhost',
        url,
        flags=re.IGNORECASE,
    )

# Tags that represent interactive or important elements
INTERACTIVE_TAGS = ["a", "button", "input", "select", "textarea", "form", "label", "nav", "h1", "h2", "h3"]
MAX_DOM_CHARS = 3000  # Keep DOM context under this limit to fit LLM context window


def _extract_elements(soup: BeautifulSoup) -> str:
    """Extract interactive elements with their attributes into a readable summary."""
    lines = []

    for tag in soup.find_all(INTERACTIVE_TAGS):
        attrs = {}

        # Core identifiers
        if tag.get("id"):
            attrs["id"] = tag["id"]
        if tag.get("class"):
            attrs["class"] = " ".join(tag["class"])
        if tag.get("name"):
            attrs["name"] = tag["name"]
        if tag.get("type"):
            attrs["type"] = tag["type"]
        if tag.get("href"):
            href = tag["href"]
            # Truncate long hrefs
            if len(href) > 80:
                href = href[:77] + "..."
            attrs["href"] = href
        if tag.get("placeholder"):
            attrs["placeholder"] = tag["placeholder"]
        if tag.get("value"):
            attrs["value"] = tag["value"][:50]
        if tag.get("role"):
            attrs["role"] = tag["role"]
        if tag.get("aria-label"):
            attrs["aria-label"] = tag["aria-label"]
        if tag.get("onclick"):
            attrs["onclick"] = tag["onclick"][:80]
        if tag.get("for"):
            attrs["for"] = tag["for"]

        # Get visible text (truncated)
        text = tag.get_text(strip=True)
        if text and len(text) > 60:
            text = text[:57] + "..."

        # Build element summary line
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        if text:
            line = f"<{tag.name} {attr_str}>{text}</{tag.name}>"
        else:
            line = f"<{tag.name} {attr_str} />"

        lines.append(line)

        # Stop if we're getting too long
        total = "\n".join(lines)
        if len(total) > MAX_DOM_CHARS:
            lines.append(f"... (truncated, {len(soup.find_all(INTERACTIVE_TAGS))} total elements)")
            break

    return "\n".join(lines)


def _fetch_dom_via_sandbox(url: str, job_id: str) -> str:
    """Fetch the fully rendered DOM of an SPA by running a Selenium script in the sandbox."""
    from pathlib import Path
    import tempfile
    from sandbox import runner
    from utils import config

    log.info("inspect_spa_fallback_start", job_id=job_id, url=url)

    script_code = f"""import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.binary_location = "/usr/bin/chromium"
service = Service("/usr/bin/chromedriver")
driver = webdriver.Chrome(service=service, options=options)

try:
    driver.get("{url}")
    time.sleep(3)  # Wait for SPA to render
    with open("/screenshots/dom.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
finally:
    driver.quit()
"""
    # Write the script to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script_code)
        script_path = f.name

    try:
        # Run it in the sandbox
        runner.run(script_path, job_id)

        # Read the generated DOM file
        dom_file = Path(config.DATA_DIR) / "artifacts" / job_id / "dom.html"
        if dom_file.exists():
            html = dom_file.read_text(encoding="utf-8")
            log.info("inspect_spa_fallback_success", job_id=job_id)
            return html
        else:
            log.warning("inspect_spa_fallback_missing_output", job_id=job_id)
            return ""
    finally:
        Path(script_path).unlink(missing_ok=True)


def inspect_node(state: AgentState) -> AgentState:
    """Fetch the target page and extract interactive DOM elements."""
    url = state["target_url"]
    job_id = state["job_id"]
    log.info("inspect_start", job_id=job_id, url=url)

    dom_context = ""
    try:
        # Step 1: Fast static fetch
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(_host_url(url), headers=headers, timeout=15, verify=False)
        resp.raise_for_status()
        html = resp.text

        soup = BeautifulSoup(html, "html.parser")
        elements = _extract_elements(soup)

        # Step 2: SPA Fallback if no interactive elements found
        if not elements.strip():
            log.info("inspect_static_empty", job_id=job_id, msg="No interactive elements found, trying SPA fallback")
            rendered_html = _fetch_dom_via_sandbox(url, job_id)
            if rendered_html:
                soup = BeautifulSoup(rendered_html, "html.parser")
                elements = _extract_elements(soup)

        title = soup.title.string.strip() if soup.title and soup.title.string else "Unknown"
        dom_context = f"Page Title: {title}\nURL: {url}\n\nInteractive elements found on the page:\n{elements}"
        log.info("inspect_success", job_id=job_id, elements_found=len(elements.splitlines()))

    except Exception as e:
        log.warning("inspect_failed", job_id=job_id, error=str(e))
        dom_context = f"(Could not fetch page DOM: {e}. The LLM must infer selectors from the bug report.)"
        raise

    return {**state, "dom_context": dom_context}
