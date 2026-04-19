"""Node 1 — LLM bug analysis: parse bug report into structured AnalysisResult JSON."""

import json
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
    """Return the configured LLM instance."""
    if config.LLM_PROVIDER == "mock":
        from utils.mock_llm import MockLLM
        return MockLLM()
    if config.LLM_PROVIDER == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(model=config.LLM_MODEL, temperature=0)
    if config.LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=config.LLM_MODEL, temperature=0)
    if config.LLM_PROVIDER == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.LLM_MODEL, temperature=0)
    if config.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=config.LLM_MODEL, temperature=0)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=config.LLM_MODEL, temperature=0)


def analyze_node(state: AgentState) -> AgentState:
    """Node 1: Parse bug report into structured AnalysisResult JSON."""
    project_root = Path(__file__).resolve().parent.parent.parent
    template = (project_root / "prompts" / "analyze.txt").read_text()
    prompt   = template.format(bug_report=state["bug_report"], target_url=state["target_url"])
    llm      = _get_llm()

    for attempt in range(2):
        response = llm.invoke(prompt)
        content  = _extract_text(response.content)
        try:
            analysis = json.loads(content)
            required = {"inferred_steps", "target_elements", "expected_behavior",
                        "success_condition", "risk_factors"}
            if not required.issubset(analysis.keys()):
                raise ValueError(f"Missing keys: {required - analysis.keys()}")
            log.info("analyze_success", job_id=state["job_id"])
            return {**state, "analysis": analysis}
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("analyze_parse_error", attempt=attempt, error=str(e))
            if attempt == 0:
                prompt += "\n\nYour previous response was not valid JSON. Return ONLY raw JSON."

    raise RuntimeError("analyze_node: LLM returned malformed JSON after 2 attempts")
