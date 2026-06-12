import requests
import json

def test():
    with open('e2e_state.json', 'r') as f:
        state = json.load(f)
    resp = requests.get('http://127.0.0.1:8000/api/v1/auth/users', headers={'Authorization': f"Bearer {state['admin_token']}"})
    print(resp.status_code)
    print(resp.json())

if __name__ == "__main__":
    test()
