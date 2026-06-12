import json
import sys
import os

# add autorepro to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api.main import app

with open("openapi.json", "w") as f:
    json.dump(app.openapi(), f, indent=2)

print("OpenAPI schema exported to openapi.json")
