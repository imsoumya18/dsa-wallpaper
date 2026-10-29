#!/usr/bin/env python3
"""
dsa-wallpaper sync server — a tiny MCP server (stdio, standard library only)
that lets a tracker page running in the Claude desktop app save your progress
straight into this folder, so the wallpaper updates the moment you tick
something. No model is involved.

It exposes one tool, `write_progress`, and can only write two files:
    <dir>/progress.json   {problem id: {"s": solved date, "flag": true, ...}}
    <dir>/catalog.json    [[id, name, "easy"|"medium"|"hard"], ...]   (optional)
<dir> defaults to ./prep-plan next to this script; pass another as argv[1].

Add it to the Claude desktop app (Settings → Developer → Edit Config):
    "mcpServers": {
      "dsa-wallpaper": {
        "command": "/path/to/dsa-wallpaper/.venv/bin/python",
        "args": ["/path/to/dsa-wallpaper/mcp_server.py"]
      }
    }
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else HERE / "prep-plan"
DIFFS = {"easy", "medium", "hard"}

TOOL = {
    "name": "write_progress",
    "description": "Save DSA progress (and optionally the problem catalog) for the dsa-wallpaper overlay. "
                   "Overwrites prep-plan/progress.json and prep-plan/catalog.json; writes nothing if unchanged.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "progress": {
                "type": "object",
                "description": "Map of problem id to its progress fields, e.g. {\"3.2.5\": {\"s\": \"2026-10-22\", \"flag\": true}}",
                "additionalProperties": {"type": "object"},
            },
            "catalog": {
                "type": "array",
                "description": "Optional problem list: [[id, name, \"easy\"|\"medium\"|\"hard\"], ...]",
                "items": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
            },
        },
        "required": ["progress"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def write_if_changed(path: Path, obj, pretty_lines=False) -> bool:
    if pretty_lines:  # one catalog row per line, like the file the repo ships
        text = "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in obj) + "\n]\n"
    else:
        text = json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) == json.loads(text):
            return False
    except Exception:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # atomic: the wallpaper never reads half a file
    return True


def validate(args):
    if not isinstance(args, dict) or set(args) - {"progress", "catalog"}:
        raise ValueError("expected {progress, catalog?}")
    prog = args.get("progress")
    if not isinstance(prog, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in prog.items()):
        raise ValueError("progress must map problem ids to objects")
    if len(prog) > 5000:
        raise ValueError("progress too large")
    cat = args.get("catalog")
    if cat is not None:
        if not isinstance(cat, list) or len(cat) > 5000:
            raise ValueError("catalog must be a list")
        for row in cat:
            if not (isinstance(row, list) and len(row) == 3 and all(isinstance(x, str) for x in row)
                    and row[2] in DIFFS):
                raise ValueError(f"bad catalog row: {row!r}")
    return prog, cat


def call_write_progress(args):
    prog, cat = validate(args)
    changed = []
    if cat is not None and write_if_changed(OUT / "catalog.json", cat, pretty_lines=True):
        changed.append("catalog")
    if write_if_changed(OUT / "progress.json", prog):
        changed.append("progress")
    solved = sum(1 for v in prog.values() if v.get("s"))
    msg = f"{'updated ' + ' and '.join(changed) if changed else 'already up to date'} · {solved} solved"
    log(msg)
    return {"content": [{"type": "text", "text": json.dumps({"ok": True, "changed": changed, "solved": solved})}],
            "structuredContent": {"ok": True, "changed": changed, "solved": solved}}


def handle(req):
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        pv = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"protocolVersion": pv, "capabilities": {"tools": {}},
                "serverInfo": {"name": "dsa-wallpaper", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        p = req.get("params") or {}
        if p.get("name") != TOOL["name"]:
            raise LookupError(f"unknown tool {p.get('name')!r}")
        try:
            return call_write_progress(p.get("arguments") or {})
        except Exception as e:  # tool-level failure, reported to the caller
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if rid is None:  # notifications (initialized, cancelled, ...) need no answer
        return None
    raise NotImplementedError(method)


def main():
    log(f"dsa-wallpaper sync server: writing to {OUT}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        try:
            result = handle(req)
            if rid is None:
                continue
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        except NotImplementedError as e:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {e}"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(e)}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
