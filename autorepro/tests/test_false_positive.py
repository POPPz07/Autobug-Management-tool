from agent.orchestrator import run_agent
from pprint import pprint

try:
    print("Testing TruthLens About tab false positive...")
    result = run_agent(
        job_id="test_truthlens_false_positive",
        bug_report="The 'About' tab shows error after clicking on that tab.",
        target_url="https://misinformation-74574.web.app/workspace"
    )
    
    print("\n--- TEST RESULT ---")
    print(f"Status: {result.get('status')}")
    print(f"Success: {result.get('success')}")
    if "execution_result" in result:
        print(f"Exit Code: {result['execution_result'].get('exit_code')}")
        print(f"Error Type: {result['execution_result'].get('error_type')}")
        print(f"Stdout:\n{result['execution_result'].get('stdout')}")
    print("\nFinal Script:")
    print(result.get("final_script"))
    
except Exception as e:
    import traceback
    print("FATAL ERROR:")
    traceback.print_exc()
