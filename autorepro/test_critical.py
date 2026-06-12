import requests
import json
import uuid

BASE_URL = "http://127.0.0.1:8000/api/v1"

def get_admin_token():
    resp = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": "admin@autorepro.dev", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    return resp.json()["access_token"]

def test_1_cross_tenant_isolation(admin_token):
    print("\n=== TEST 1: Cross-Tenant Isolation ===")
    
    admin_headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    
    # Get FREE plan from DB
    import psycopg2
    conn = psycopg2.connect("postgresql://autorepro:autorepro@127.0.0.1:5432/autorepro")
    cur = conn.cursor()
    cur.execute("SELECT id FROM subscription_plans WHERE name = 'FREE';")
    free_plan_id = str(cur.fetchone()[0])
    conn.close()
    
    # Create Company A
    resp_a = requests.post(f"{BASE_URL}/platform/companies", headers=admin_headers, json={"name": "Company A", "slug": f"comp-a-{uuid.uuid4().hex[:6]}", "plan_id": free_plan_id})
    comp_a = resp_a.json()["data"]
    
    # Create Company B
    resp_b = requests.post(f"{BASE_URL}/platform/companies", headers=admin_headers, json={"name": "Company B", "slug": f"comp-b-{uuid.uuid4().hex[:6]}", "plan_id": free_plan_id})
    comp_b = resp_b.json()["data"]
    
    # Register User A in Company A
    email_a = f"usera_{uuid.uuid4().hex[:6]}@compa.com"
    resp_a = requests.post(f"{BASE_URL}/auth/register", json={"email": email_a, "password": "password", "company_id": comp_a["id"], "full_name": "User A"})
    user_a_id = resp_a.json()["id"]
    requests.patch(f"{BASE_URL}/auth/users/{user_a_id}/role", headers={"Authorization": f"Bearer {admin_token}"}, json={"role": "ORG_ADMIN"})
    token_a = requests.post(f"{BASE_URL}/auth/login", data={"username": email_a, "password": "password"}, headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    
    # Register User B in Company B
    email_b = f"userb_{uuid.uuid4().hex[:6]}@compb.com"
    requests.post(f"{BASE_URL}/auth/register", json={"email": email_b, "password": "password", "company_id": comp_b["id"], "full_name": "User B"})
    token_b = requests.post(f"{BASE_URL}/auth/login", data={"username": email_b, "password": "password"}, headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    
    # User A creates a bug
    resp = requests.post(f"{BASE_URL}/bugs", headers={"Authorization": f"Bearer {token_a}"}, json={"title": "Bug A", "description": "Bug for Company A", "target_url": "http://a.com"})
    bug_a_id = resp.json()["data"]["id"]
    
    # User B tries to fetch Bug A
    resp_b = requests.get(f"{BASE_URL}/bugs/{bug_a_id}", headers={"Authorization": f"Bearer {token_b}"})
    print(f"User B fetching Bug A: Status {resp_b.status_code}")
    assert resp_b.status_code in [403, 404]

    # User B tries to search and find Bug A
    resp_search = requests.get(f"{BASE_URL}/bugs/search?q=Bug A", headers={"Authorization": f"Bearer {token_b}"})
    found_bugs = resp_search.json().get("data", [])
    print(f"User B searching for 'Bug A': Found {len(found_bugs)} results")
    assert len(found_bugs) == 0
    print("SUCCESS: TEST 1 PASSED")
    return comp_a, token_a, bug_a_id, user_a_id

def test_2_role_permissions(admin_token, comp_a):
    print("\n=== TEST 2: Role Permissions ===")
    
    # Need an ORG_ADMIN token to assign roles (or admin can do it)
    email_dev = f"dev_{uuid.uuid4().hex[:6]}@compa.com"
    resp = requests.post(f"{BASE_URL}/auth/register", json={"email": email_dev, "password": "password", "company_id": comp_a["id"], "full_name": "Dev User"})
    if resp.status_code not in [200, 201]:
        print("Registration failed:", resp.text)
    dev_id = resp.json()["id"]
    
    # Update role to DEVELOPER (Platform admin can do it)
    requests.patch(f"{BASE_URL}/auth/users/{dev_id}/role", headers={"Authorization": f"Bearer {admin_token}"}, json={"role": "DEVELOPER"})
    
    token_dev = requests.post(f"{BASE_URL}/auth/login", data={"username": email_dev, "password": "password"}, headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    
    # Developer tries to create a bug (should work)
    resp_bug = requests.post(f"{BASE_URL}/bugs", headers={"Authorization": f"Bearer {token_dev}"}, json={"title": "Bug Dev", "description": "Dev bug", "target_url": "http://dev.com"})
    print(f"Developer creating bug: Status {resp_bug.status_code}")
    bug_dev_id = resp_bug.json()["data"]["id"]
    
    # Developer tries to run AutoRepro
    resp_job = requests.post(f"{BASE_URL}/jobs/trigger", headers={"Authorization": f"Bearer {token_dev}"}, json={"bug_id": bug_dev_id})
    print(f"Developer triggering AutoRepro: Status {resp_job.status_code} - {resp_job.text}")
    assert resp_job.status_code == 403
    print("SUCCESS: TEST 2 PASSED")
    return token_dev, dev_id

def test_3_quota_limits(admin_token, comp_a, token_a, user_a_id, token_dev, dev_id):
    print("\n=== TEST 3: Quota Limits ===")
    
    success_count = 0
    fail_count = 0
    for i in range(12):
        # Create a new bug for each trigger to avoid JOB_ALREADY_RUNNING
        resp_bug = requests.post(f"{BASE_URL}/bugs", headers={"Authorization": f"Bearer {token_a}"}, json={"title": f"Quota Bug {i}", "description": "desc", "target_url": "http://q.com"})
        b_id = resp_bug.json()["data"]["id"]
        
        # Setup bug state
        requests.post(f"{BASE_URL}/bugs/{b_id}/assign", headers={"Authorization": f"Bearer {admin_token}"}, json={"assigned_to_user_id": dev_id})
        requests.patch(f"{BASE_URL}/bugs/{b_id}/transition", headers={"Authorization": f"Bearer {admin_token}"}, json={"new_status": "TRIAGED"})
        requests.patch(f"{BASE_URL}/bugs/{b_id}/transition", headers={"Authorization": f"Bearer {admin_token}"}, json={"new_status": "ASSIGNED"})
        requests.patch(f"{BASE_URL}/bugs/{b_id}/transition", headers={"Authorization": f"Bearer {admin_token}"}, json={"new_status": "IN_PROGRESS"})
        
        resp = requests.post(f"{BASE_URL}/jobs/trigger", headers={"Authorization": f"Bearer {token_a}"}, json={"bug_id": b_id})
        if resp.status_code in [200, 202]:
            success_count += 1
        elif resp.status_code == 429:
            fail_count += 1
            print(f"Request {i+1} failed with 429: {resp.json().get('detail')}")
        else:
            print(f"Request {i+1} failed unexpectedly with {resp.status_code}: {resp.text}")
    
    print(f"Successful triggers: {success_count}, Rate-limited: {fail_count}")
    assert fail_count > 0
    print("SUCCESS: TEST 3 PASSED")

def test_4_platform_admin(admin_token):
    print("\n=== TEST 4: Platform Admin ===")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    resp = requests.get(f"{BASE_URL}/platform/companies", headers=admin_headers)
    companies = resp.json().get("data", [])
    print(f"Admin listed {len(companies)} companies.")
    assert len(companies) > 1
    
    # Get a random user to impersonate
    users_resp = requests.get(f"{BASE_URL}/auth/users", headers=admin_headers)
    users = users_resp.json()
    target_user_id = [u for u in users if u["email"] != "admin@autorepro.dev"][0]["id"]
    
    imp_resp = requests.post(f"{BASE_URL}/platform/impersonate/{target_user_id}", headers=admin_headers)
    print(f"Admin impersonating user: Status {imp_resp.status_code}")
    assert imp_resp.status_code == 200
    imp_token = imp_resp.json()["data"]["access_token"]
    assert imp_token != admin_token
    print("SUCCESS: TEST 4 PASSED")

def test_5_api_keys(token_a):
    print("\n=== TEST 5: API Keys ===")
    # Use ORG_ADMIN token (token_a)
    resp = requests.post(f"{BASE_URL}/auth/api-keys", headers={"Authorization": f"Bearer {token_a}"}, json={"name": "Test Pipeline Key"})
    if resp.status_code == 404:
        # Maybe it's at /api-keys directly
        resp = requests.post(f"{BASE_URL}/api-keys/", headers={"Authorization": f"Bearer {token_a}"}, json={"name": "Test Pipeline Key"})
    
    print(f"Create API Key Status: {resp.status_code}")
    if resp.status_code != 200 and resp.status_code != 201:
        print(f"Failed to create key: {resp.text}")
        return
        
    api_key = resp.json()["data"]["raw_key"]
    
    # Try using the key
    resp_bug = requests.post(f"{BASE_URL}/bugs", headers={"X-API-Key": api_key}, json={"title": "Bug via API Key", "description": "desc", "target_url": "http://api.com"})
    print(f"Create Bug with API Key Status: {resp_bug.status_code}")
    assert resp_bug.status_code in [200, 201]
    print("SUCCESS: TEST 5 PASSED")

if __name__ == "__main__":
    admin_token = get_admin_token()
    comp_a, token_a, bug_a_id, user_a_id = test_1_cross_tenant_isolation(admin_token)
    token_dev, dev_id = test_2_role_permissions(admin_token, comp_a)
    test_3_quota_limits(admin_token, comp_a, token_a, user_a_id, token_dev, dev_id)
    test_4_platform_admin(admin_token)
    test_5_api_keys(token_a)
