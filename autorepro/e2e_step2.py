import requests
import json
import sys
import time
import os

BASE_URL = "http://127.0.0.1:8000/api/v1"
WEBHOOK_URL = "https://webhook.site/d276f5de-91d8-4f9e-a0df-2605f6b8b549" # example

def run_steps():
    if not os.path.exists("e2e_state.json"):
        print("Run e2e_step1.py first")
        sys.exit(1)
        
    with open("e2e_state.json", "r") as f:
        state = json.load(f)
        
    admin_token = state["admin_token"]
    company_id = state["company_id"]
    admin_headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    
    print("\n--- E2E Step 2: Register User ---")
    reg_data = {
        "full_name": "Test User",
        "email": "testuser@test.corp",
        "password": "password123",
        "role": "DEVELOPER",
        "company_id": company_id
    }
    resp = requests.post(f"{BASE_URL}/auth/register", json=reg_data)
    if resp.status_code == 201:
        user_id = resp.json()["id"]
        print(f"User registered (ID: {user_id})")
    elif resp.status_code == 409:
        print("User already exists, getting ID...")
        # Get users
        u_resp = requests.get(f"{BASE_URL}/auth/users", headers=admin_headers)
        for u in u_resp.json():
            if u["email"] == "testuser@test.corp":
                user_id = u["id"]
                break
        print(f"Found User (ID: {user_id})")
    else:
        print(f"Failed to register: {resp.text}")
        sys.exit(1)
        
    print("\n--- Elevating User to ORG_ADMIN ---")
    resp = requests.patch(f"{BASE_URL}/auth/users/{user_id}/role", headers=admin_headers, json={"role": "ORG_ADMIN"})
    if resp.status_code == 200:
        print("User elevated to ORG_ADMIN")
    else:
        print(f"Failed to elevate: {resp.text}")

    print("\n--- Registering secondary DEV user for assignment ---")
    dev_data = {
        "full_name": "Test Dev",
        "email": "dev2@test.corp",
        "password": "password123",
        "role": "DEVELOPER",
        "company_id": company_id
    }
    resp = requests.post(f"{BASE_URL}/auth/register", json=dev_data)
    if resp.status_code == 201:
        dev_id = resp.json()["id"]
    else:
        # Get existing
        u_resp = requests.get(f"{BASE_URL}/auth/users", headers=admin_headers)
        for u in u_resp.json():
            if u["email"] == "dev2@test.corp":
                dev_id = u["id"]
                break
    print(f"Dev User ID: {dev_id}")

    print("\n--- Logging in as Test User (ORG_ADMIN) ---")
    resp = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": "testuser@test.corp", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    user_token = resp.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"}
    print("Logged in successfully")

    print("\n--- Creating Webhook ---")
    wh_data = {
        "name": "Test Webhook",
        "url": WEBHOOK_URL,
        "events": ["bug.assigned", "job.completed", "job.failed", "bug.status_changed"],
        "secret": "mysecret",
        "description": "E2E Test Webhook"
    }
    resp = requests.post(f"{BASE_URL}/webhooks/", headers=user_headers, json=wh_data)
    if resp.status_code in [200, 201]:
        print("Webhook created")
    else:
        print(f"Failed to create webhook: {resp.text}")

    print("\n--- E2E Step 3: Create Bug ---")
    bug_data = {
        "title": "E2E Test Bug SearchKeyword123",
        "description": "This is a test bug for E2E verification. Go to https://example.com and click.",
        "target_url": "https://example.com",
        "severity": "HIGH",
        "status": "CREATED"
    }
    resp = requests.post(f"{BASE_URL}/bugs", headers=user_headers, json=bug_data)
    if resp.status_code in [200, 201]:
        bug_id = resp.json()["data"]["id"]
        print(f"Bug created (ID: {bug_id})")
    else:
        print(f"Failed to create bug: {resp.text}")
        sys.exit(1)

    print("\n--- E2E Step 4: Assign Bug ---")
    resp = requests.post(f"{BASE_URL}/bugs/{bug_id}/assign", headers=user_headers, json={"assigned_to_user_id": dev_id, "note": "E2E Test Assignment"})
    if resp.status_code == 200:
        print("Bug assigned to dev (this fires webhook and notification)")
    else:
        print(f"Failed to assign bug: {resp.text}")

    print("\n--- E2E Step 4.1: Transition to TRIAGED ---")
    resp = requests.patch(f"{BASE_URL}/bugs/{bug_id}/transition", headers=user_headers, json={"new_status": "TRIAGED"})
    
    print("\n--- E2E Step 4.2: Transition to ASSIGNED ---")
    resp = requests.patch(f"{BASE_URL}/bugs/{bug_id}/transition", headers=user_headers, json={"new_status": "ASSIGNED"})
    
    print("\n--- E2E Step 4.3: Transition to IN_PROGRESS ---")
    # Wait, ORG_ADMIN might not be allowed to transition to IN_PROGRESS if that's developer restricted? 
    # Actually, ORG_ADMIN >= TESTER (which is required for IN_PROGRESS).
    resp = requests.patch(f"{BASE_URL}/bugs/{bug_id}/transition", headers=user_headers, json={"new_status": "IN_PROGRESS"})
    if resp.status_code == 200:
        print("Bug transitioned to IN_PROGRESS")
    else:
        print(f"Failed to transition bug: {resp.text}")

    print("\n--- E2E Step 5: Trigger AutoRepro Job ---")
    resp = requests.post(f"{BASE_URL}/jobs/trigger", headers=user_headers, json={"bug_id": bug_id, "priority": "NORMAL"})
    if resp.status_code in [200, 201, 202]:
        job_id = resp.json()["data"]["id"]
        print(f"Job triggered (ID: {job_id})")
    else:
        print(f"Failed to trigger job: {resp.status_code} - {resp.text}")
        sys.exit(1)

    print("\n--- E2E Step 6: Wait and Verify Job ---")
    print("Waiting for job to complete (polling)...")
    for _ in range(15):
        time.sleep(5)
        resp = requests.get(f"{BASE_URL}/jobs/{job_id}", headers=user_headers)
        if resp.status_code == 200:
            status = resp.json()["data"]["status"]
            print(f"Job status: {status}")
            if status in ["SUCCESS", "FAILED"]:
                screenshots = resp.json()["data"].get("screenshots", [])
                logs = resp.json()["data"].get("logs", "")
                print(f"Job Finished!")
                print(f"Screenshots count: {len(screenshots)}")
                if len(screenshots) == 0:
                    print("WARNING: No screenshots populated. Bridge might have failed.")
                else:
                    print("Job.screenshots populated!")
                break
    else:
        print("Timeout waiting for job to complete.")

    print("\n--- E2E Step 7: Search Bugs by Keyword ---")
    resp = requests.get(f"{BASE_URL}/bugs/search?q=SearchKeyword123", headers=user_headers)
    if resp.status_code == 200:
        results = resp.json()["data"]
        if len(results) > 0:
            print(f"Search found {len(results)} bug(s) via GIN index!")
        else:
            print("Search returned 0 results.")
    else:
        print(f"Search failed: {resp.text}")

if __name__ == "__main__":
    run_steps()
