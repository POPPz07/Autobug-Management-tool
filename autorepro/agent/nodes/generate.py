"""Node 2 — LLM script generation: analysis → Python/Selenium script."""

import ast
import json
import re
from pathlib import Path

from agent.state import AgentState
from utils import config
from utils.logger import get_logger

log = get_logger(__name__)


def _extract_text(content) -> str:
    """Safely extract text from an LLM response's .content field.

    ChatBedrockConverse returns a list of content blocks like
    [{"type": "text", "text": "..."}], while other providers return
    a plain string. This handles both.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content).strip()


def _get_llm():
    """Return the configured LLM instance with slight temperature for creativity."""
    if config.LLM_PROVIDER == "mock":
        from utils.mock_llm import MockLLM
        return MockLLM()
    if config.LLM_PROVIDER == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(model=config.LLM_MODEL, temperature=0.2)
    if config.LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=config.LLM_MODEL, temperature=0.2)
    if config.LLM_PROVIDER == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.LLM_MODEL, temperature=0.2)
    if config.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=config.LLM_MODEL, temperature=0.2)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=config.LLM_MODEL, temperature=0.2)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences and extract Python code."""
    # Try to extract from ```python ... ``` blocks first
    fence_pattern = re.compile(r'```(?:python)?\s*\n(.*?)\n```', re.DOTALL)
    matches = fence_pattern.findall(text)
    if matches:
        return matches[-1].strip()
    # Fallback: strip leading/trailing fences
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _fix_url(script: str, target_url: str) -> str:
    """Ensure the script uses the exact target URL, not LLM-rewritten variants."""
    import re
    pattern = re.compile(r'(driver\.get\(|TARGET_URL\s*=\s*)["\']https?://(?:localhost|127\.0\.0\.1)[^"\']*["\']')
    return pattern.sub(rf'\1"{target_url}"', script)


def generate_node(state: AgentState) -> AgentState:
    """Node 2: Generate a Python/Selenium script from the structured analysis."""
    prior = "\n".join(
        f"Attempt {h['attempt']}: {h.get('refinement_note', 'No note')}"
        for h in state["history"]
    ) or "None"

    project_root = Path(__file__).resolve().parent.parent.parent
    template = (project_root / "prompts" / "generate.txt").read_text()
    prompt   = template.format(
        analysis_json=json.dumps(state["analysis"], indent=2),
        target_url=state["target_url"],
        dom_context=state.get("dom_context", "Not available"),
        prior_failures=prior,
    )
    llm = _get_llm()

    for attempt in range(2):
        response = llm.invoke(prompt)
        script   = _strip_fences(_extract_text(response.content))
        script   = _fix_url(script, state["target_url"])
        try:
            ast.parse(script)
            log.info("generate_success", job_id=state["job_id"])
            return {**state, "script": script}
        except SyntaxError as e:
            log.warning("generate_syntax_error", attempt=attempt, error=str(e))
            if attempt == 0:
                prompt += f"\n\nSyntax error: {e}. Fix it and return ONLY the corrected script."

    return {**state, "script": script}

