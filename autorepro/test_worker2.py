import psycopg2
from agent.orchestrator import run_agent

def test_run():
    job_id = "c915f19e-ee39-4cdf-88d6-722565d50fa9"
    
    conn = psycopg2.connect('postgresql://autorepro:autorepro@127.0.0.1:5432/autorepro')
    cur = conn.cursor()
    cur.execute(f"SELECT llm_used FROM jobs WHERE id = '{job_id}'")
    llm_used_str = cur.fetchone()[0] or "gemini/gemini-2.0-flash"
    
    print(f"Agent using LLM: {llm_used_str}")
    
    from utils import config
    provider, model = llm_used_str.split("/", 1)
    config.LLM_PROVIDER = provider
    config.LLM_MODEL = model
    
    try:
        print("Running agent...")
        result = run_agent(
            bug_report="Test Bug",
            target_url="https://example.com",
            job_id=job_id
        )
        print("Result:", result)
    except Exception as e:
        print("Exception caught outside run_agent:", e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_run()
