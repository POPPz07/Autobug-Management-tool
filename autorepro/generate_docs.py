import json
import os

with open('openapi.json', 'r') as f:
    schema = json.load(f)

def get_ref_name(ref):
    if not ref: return ""
    return ref.split('/')[-1]

def resolve_schema(schema_obj):
    if '$ref' in schema_obj:
        ref_name = get_ref_name(schema_obj['$ref'])
        return schema['components']['schemas'][ref_name], ref_name
    return schema_obj, None

def format_type(prop):
    if '$ref' in prop:
        return get_ref_name(prop['$ref'])
    if 'anyOf' in prop:
        types = [format_type(t) for t in prop['anyOf']]
        return " | ".join(types)
    t = prop.get('type', 'any')
    if t == 'array':
        item_type = format_type(prop.get('items', {}))
        return f"array[{item_type}]"
    if t == 'string' and prop.get('format'):
        return f"string({prop['format']})"
    return t

def render_schema(schema_obj):
    if not schema_obj:
        return "{}"
    if 'properties' not in schema_obj:
        if 'type' in schema_obj:
            return format_type(schema_obj)
        return "{}"
    
    lines = []
    lines.append("{")
    req = set(schema_obj.get('required', []))
    for pname, pval in schema_obj.get('properties', {}).items():
        opt = "" if pname in req else "?"
        ptype = format_type(pval)
        lines.append(f"  {pname}{opt}: {ptype}")
    lines.append("}")
    return "\n".join(lines)

def generate_endpoint_doc(path, method, details):
    md = f"### `{method.upper()} {path}`\n\n"
    if 'summary' in details:
        md += f"**Summary:** {details['summary']}\n\n"
    if 'description' in details:
        md += f"{details['description']}\n\n"
    
    # Headers
    md += "**Required Headers:**\n"
    md += "- `Authorization`: Bearer <token>\n"
    md += "- `Content-Type`: application/json\n\n"
    
    # Parameters
    if 'parameters' in details:
        md += "**Query/Path Parameters:**\n"
        for param in details['parameters']:
            req = "Required" if param.get('required') else "Optional"
            ptype = format_type(param.get('schema', {}))
            desc = param.get('description', '')
            md += f"- `{param['name']}` ({param['in']}, {ptype}) [{req}]: {desc}\n"
        md += "\n"
    
    # Request Body
    if 'requestBody' in details:
        content = details['requestBody'].get('content', {})
        if 'application/json' in content:
            req_schema = content['application/json'].get('schema', {})
            resolved, ref_name = resolve_schema(req_schema)
            md += "**Request Body Schema"
            if ref_name: md += f" ({ref_name})"
            md += ":**\n```ts\n"
            md += render_schema(resolved)
            md += "\n```\n\n"
        elif 'multipart/form-data' in content:
            md += "**Request Body:** `multipart/form-data`\n\n"
        elif 'application/x-www-form-urlencoded' in content:
            md += "**Request Body:** `application/x-www-form-urlencoded`\n\n"
    
    # Responses
    md += "**Responses:**\n"
    for status_code, resp in details.get('responses', {}).items():
        desc = resp.get('description', '')
        md += f"- **{status_code}**: {desc}\n"
        content = resp.get('content', {})
        if 'application/json' in content:
            resp_schema = content['application/json'].get('schema', {})
            resolved, ref_name = resolve_schema(resp_schema)
            md += f"  - Body: `{ref_name if ref_name else 'object'}`\n"
    
    md += "\n---\n\n"
    return md

groups = {
    "1. AUTHENTICATION & USER MANAGEMENT": ['auth'],
    "2. BUG MANAGEMENT": ['bugs', 'comments'],
    "3. AUTOREPRO JOB EXECUTION": ['jobs'],
    "4. TEAMS & ASSIGNMENTS": ['teams'],
    "5. PLATFORM ADMIN": ['platform', 'system', 'api-keys'],
    "6. NOTIFICATIONS": ['notifications'],
    "7. WEBHOOKS": ['webhooks'],
    "8. TEMPLATES & LABELS": ['templates', 'labels'],
    "9. BULK OPERATIONS": ['bulk'],
    "10. REAL-TIME WEBSOCKET": ['realtime'] # We'll append custom text here too
}

def get_group(tags):
    if not tags: return "OTHER"
    for t in tags:
        for k, v in groups.items():
            if t in v:
                return k
    return "OTHER"

endpoints_by_group = {k: [] for k in groups.keys()}
endpoints_by_group["OTHER"] = []

for path, methods in schema['paths'].items():
    for method, details in methods.items():
        tags = details.get('tags', [])
        group = get_group(tags)
        endpoints_by_group[group].append((path, method, details))

output = []
output.append("# AUTOREPRO ENTERPRISE - COMPLETE BACKEND API DOCUMENTATION\n")
output.append("> Auto-generated from OpenAPI schema and system models.\n\n")

for group_name in groups.keys():
    output.append(f"## {group_name}\n\n")
    if group_name == "10. REAL-TIME WEBSOCKET":
        output.append("""
**WebSocket Connections:**
- `WS /ws` : Connect using a token via query parameter (e.g., `ws://localhost:8000/ws?token=<JWT>`).
- Subscribes client to all events for their authenticated User ID, Team ID, and Company ID.

**Server-Sent Events (SSE):**
- `GET /api/v1/events/stream` : Alternative to WS, uses HTTP SSE for real-time events.

**Event Payload Schema:**
All real-time events follow this JSON structure:
```json
{
  "type": "event_type_string",
  "data": {
    "job_id": "uuid",
    "bug_id": "uuid",
    "status": "...",
    "progress_percent": 100,
    ...
  },
  "timestamp": "ISO-8601 string"
}
```

**Common Event Types:**
- `job.started`: AutoRepro job has begun.
- `job.progress`: Live update (steps, tokens, percent).
- `job.completed`: Job finished successfully.
- `job.failed`: Job failed.
- `bug.created`, `bug.assigned`, `comment.created` etc.
\n""")
        
    for path, method, details in endpoints_by_group[group_name]:
        output.append(generate_endpoint_doc(path, method, details))

# User Roles & Permissions
output.append("## 11. USER ROLES & PERMISSIONS\n\n")
output.append("""
The system enforces a strict 6-tier RBAC hierarchy. A user with a higher role inherits all permissions of lower roles within their tenant.

- **DEVELOPER (1)**: Can view bugs, update bug status, comment.
- **TESTER (2)**: Inherits Developer. Can trigger AutoRepro jobs, create bugs.
- **SUPERVISOR (3)**: Inherits Tester. Can manage their assigned team, assign bugs to team members.
- **MANAGER (4)**: Inherits Supervisor. Can oversee multiple teams, view org-wide metrics.
- **ORG_ADMIN (5)**: Inherits Manager. Company owner. Can manage all users, billing, settings.
- **PLATFORM_ADMIN (6)**: Superuser. Cross-tenant access, manages platform health and subscriptions.
- **SYSTEM (7)**: Internal backend worker role. Not assignable to humans.

*All API endpoints validate these roles based on the injected JWT token.*
\n""")

# Data Models
output.append("## 12. DATA MODELS & ENUMS\n\n")
schemas = schema['components']['schemas']

output.append("### ENUMS\n")
for name, sch in schemas.items():
    if 'enum' in sch:
        output.append(f"**{name}**: `{', '.join([str(e) for e in sch['enum']])}`\n")
        
output.append("\n### MODELS\n")
for name, sch in schemas.items():
    if 'enum' not in sch and ('Public' in name or 'Create' in name or 'Update' in name):
        output.append(f"**{name}**:\n```ts\n{render_schema(sch)}\n```\n")

# Workflows
output.append("## 13. WORKFLOWS\n\n")
output.append("""
### Bug Lifecycle
Bugs follow a strict linear progression defined by `BugStatus`.
`CREATED` -> `TRIAGED` -> `ASSIGNED` -> `IN_PROGRESS` -> `RUNNING_AUTOREPRO` -> `RESOLVED` | `CLOSED` | `DUPLICATE`
- *Note:* `RUNNING_AUTOREPRO` is a transient state set exclusively by the backend worker. Users cannot manually transition a bug into this state via API.

### Job Execution Flow
1. User triggers job (`POST /api/v1/jobs/trigger`). Job is created in DB with status `PENDING`.
2. Job is pushed to Redis queue.
3. Worker picks up job, transitions Job to `RUNNING` and Bug to `RUNNING_AUTOREPRO`.
4. Worker executes steps (Analysis, DOM parsing, Script generation, Execution, Verification).
5. Live progress is broadcasted via WebSockets/SSE (`job.progress`).
6. Worker finalizes job. Job status becomes `SUCCESS` or `FAILED`. Bug status reverts to `IN_PROGRESS` or transitions to `RESOLVED` (if success score > 0.8).

### Assignment Flow
When a bug is assigned via `POST /api/v1/bugs/{id}/assign`:
1. System validates the assignee belongs to the same company (and optionally team).
2. Creates a `BugAssignment` history record.
3. Updates `current_assignee_id` on the Bug record.
4. Triggers `bug.assigned` real-time event and possible email notification.
""")

# Error Handling
output.append("## 14. ERROR HANDLING\n\n")
output.append("""
All API errors return a standardized JSON envelope:

```json
{
  "error": true,
  "code": "STRING_ERROR_CODE",
  "message": "Human readable description",
  "details": { "optional": "context" }
}
```

**Common HTTP Status Codes:**
- `400 Bad Request`: Validation errors, invalid state transitions.
- `401 Unauthorized`: Missing or invalid JWT/API Key.
- `403 Forbidden`: Insufficient RBAC role, or attempting to access cross-tenant data.
- `404 Not Found`: Resource does not exist or belongs to another company.
- `429 Too Many Requests`: Rate limit exceeded for the user/company.
- `500 Internal Server Error`: Unhandled backend exception.
""")

with open('../BACKEND_FEATURES_COMPLETE.md', 'w') as f:
    f.write("\n".join(output))

print("Documentation generated at BACKEND_FEATURES_COMPLETE.md")
