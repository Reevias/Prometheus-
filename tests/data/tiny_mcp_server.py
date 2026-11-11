"""A tiny MCP server for tests: speaks JSON-RPC over stdin/stdout like real ones do."""
import json
import os
import sys

TOOLS = [{"name": "search_notes", "description": "Search notes", "annotations": {"readOnlyHint": True},
          "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
         {"name": "add_note", "description": "Add a note", "inputSchema": {"type": "object"}}]
NOTES = ["Buy milk", "Prometheus plan"]

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "tiny", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        args = params.get("arguments") or {}
        if params["name"] == "search_notes":
            hits = [n for n in NOTES if args.get("q", "").lower() in n.lower()]
            result = {"content": [{"type": "text", "text": "\n".join(hits) or "nothing"}]}
        else:
            NOTES.append(args.get("text", ""))
            result = {"content": [{"type": "text", "text": f"added {args.get('text')} (token {os.environ.get('TINY_TOKEN', '-')})"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
