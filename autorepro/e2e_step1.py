import requests
import json
import sys
import psycopg2

BASE_URL = "http://127.0.0.1:8000/api/v1"

def run_step_1():
    print("--- E2E Step 1: Create Company (Platform Admin) ---")
    
    # 1. Login as Platform Admin
    print("Logging in as admin...")
    login_resp = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": "admin@autorepro.dev", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if login_resp.status_code != 200:
        print(f"Login failed: {login_resp.status_code} - {login_resp.text}")
        sys.exit(1)
        
    admin_token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    print("Login successful, got admin token.")

    # 2. Get the FREE plan ID from database
    print("Fetching subscription plans from database...")
    try:
        conn = psycopg2.connect("postgresql://autorepro:autorepro@127.0.0.1:5432/autorepro")
        cur = conn.cursor()
        cur.execute("SELECT id FROM subscription_plans WHERE name = 'FREE';")
        row = cur.fetchone()
        conn.close()
        if not row:
            print("FREE plan not found in database.")
            sys.exit(1)
        free_plan_id = str(row[0])
        print(f"Found FREE plan (ID: {free_plan_id})")
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

    # 3. Create Company
    print("Creating company 'Test Corp'...")
    company_data = {
        "name": "Test Corp",
        "slug": "test-corp",
        "plan_id": free_plan_id
    }
    comp_resp = requests.post(f"{BASE_URL}/platform/companies", headers=headers, json=company_data)
    
    if comp_resp.status_code in [200, 201]:
        print(f"Company created successfully! Status: {comp_resp.status_code}")
        company_id = comp_resp.json()["data"]["id"]
    elif comp_resp.status_code in [409, 500]:
        print("Company already exists (or ValueError), fetching ID...")
        list_resp = requests.get(f"{BASE_URL}/platform/companies", headers=headers)
        companies = list_resp.json().get("data", [])
        company_id = next((c["id"] for c in companies if c["slug"] == "test-corp"), None)
        if not company_id:
            print(f"Failed to find existing company test-corp. Original error: {comp_resp.text}")
            sys.exit(1)
    else:
        print(f"Failed to create company: {comp_resp.status_code} - {comp_resp.text}")
        sys.exit(1)

    # Save token and company id for step 2
    with open("e2e_state.json", "w") as f:
        json.dump({
            "admin_token": admin_token,
            "company_id": company_id,
            "free_plan_id": free_plan_id
        }, f)
    print(f"State saved to e2e_state.json (Company ID: {company_id})")

if __name__ == "__main__":
    run_step_1()
