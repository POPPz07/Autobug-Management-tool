import asyncio
import os
import uuid
from agent.orchestrator import run_agent
from db.models import Bug

def test_run():
    # Pass job_id as string
    job_id = "c915f19e-ee39-4cdf-88d6-722565d50fa9"
    print("Running agent...")
    try:
        from utils import config
        config.LLM_PROVIDER = "groq"
        config.LLM_MODEL = "llama-3.1-70b"
        
        result = run_agent(
            bug_report="Test Bug",
            target_url="https://example.com",
            job_id=job_id
        )
        print("Result:", result)
    except Exception as e:
        print("Exception:", e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_run()
