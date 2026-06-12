import json
import os

with open('openapi.json', 'r') as f:
    schema = json.load(f)

tags = set()
for path, methods in schema['paths'].items():
    for method, details in methods.items():
        if 'tags' in details:
            tags.update(details['tags'])
print("Tags found:", tags)
