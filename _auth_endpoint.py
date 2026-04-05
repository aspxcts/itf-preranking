#!/usr/bin/env python3
# !! RETIRED — replaced by /api/login in app.py (FastAPI) !!
# Delete this file manually: del _auth_endpoint.py
raise SystemExit("_auth_endpoint.py has been retired. See app.py /api/login.")


Usage (from Node.js):
    exec('python3 _auth_endpoint.py', {
      input: JSON.stringify({email, password}),
      ...
    }, callback)

Expects JSON on stdin:
    {"email": "user@example.com", "password": "password"}

Outputs JSON to stdout (only!):
    {
      "ok": true,
      "cookies": {name: value, ...},
      "email": "user@example.com"
    }
    OR
    {
      "ok": false,
      "error": "Login failed: {details}"
    }

Debug messages sent to stderr (node ignores).
"""

import asyncio
import json
import sys
from src.auth import login


async def main():
    try:
        # Read JSON from stdin
        line = sys.stdin.read()
        data = json.loads(line)
        email = data.get("email", "").strip()
        password = data.get("password", "")

        if not email or not password:
            # Output JSON to stdout only
            print(json.dumps({"ok": False, "error": "Missing email or password"}))
            return

        # Run login (auth module prints debug to stderr now)
        result = await login(email, password)
        if result:
            # Output JSON to stdout
            print(json.dumps({
                "ok": True,
                "cookies": result["cookies"],
                "email": result["email"],
            }))
        else:
            print(json.dumps({
                "ok": False,
                "error": "Login failed: incorrect credentials or timeout",
            }))

    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": f"Error: {str(e)}",
        }))


if __name__ == "__main__":
    asyncio.run(main())
