"""The FaaS caller — a fixed, package-shipped script the model can never
edit (it lives in otakuchat's own source tree, not the user's writable
FUNCTIONS.py). Runs one function call in an isolated subprocess so a
crash, infinite loop, or hostile function body can't take the TUI process
down with it.

Invoked exactly as: `python -m otakuchat.faas_caller <functions_file> <func_name> <json_kwargs>`
Never invoked directly by the model — otakuchat/faas.py's run_function()
is the only caller, and only after the user has explicitly approved the
specific call (see faas_ui.py's confirmation flow).

Prints exactly one JSON line to stdout: {"ok": true, "result": ...} or
{"ok": false, "error": "..."}. Any stray prints from the user's own
function body land in the same stdout stream ahead of that JSON line, so
callers should parse the LAST line, not assume stdout is JSON-only.
"""
from __future__ import annotations

import importlib.util
import json
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(json.dumps({"ok": False, "error": "usage: faas_caller.py <functions_file> <func_name> <json_kwargs>"}))
        return 1

    functions_file, func_name, json_kwargs = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        kwargs = json.loads(json_kwargs)
        if not isinstance(kwargs, dict):
            raise ValueError("kwargs must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"bad kwargs: {e}"}))
        return 1

    try:
        spec = importlib.util.spec_from_file_location("otaku_user_functions", functions_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load {functions_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 — surface any load-time failure as a call result, never crash
        print(json.dumps({"ok": False, "error": f"failed to load functions file: {e}"}))
        return 1

    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        print(json.dumps({"ok": False, "error": f"no callable function named '{func_name}'"}))
        return 1

    try:
        result = func(**kwargs)
        # Best-effort JSON-ability check up front so a non-serializable
        # return value gets a clear error instead of a stack trace.
        json.dumps(result, default=str)
        print(json.dumps({"ok": True, "result": result}, default=str))
        return 0
    except Exception as e:  # noqa: BLE001 — the whole point: never let a user function's exception crash us, report it
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
