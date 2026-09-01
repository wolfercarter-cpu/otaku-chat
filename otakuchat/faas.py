"""FaaS "hands": lets even a small/non-tool-calling local model act on the
user's own functions, ported from Otakumafia's faas-boy/faas-boy-light
projects (inspiration/faas-boy*) but re-architected around this project's
constraints:

- faas-boy wrapped user code in FastAPI + uvicorn and served it as a
  local HTTP API the model would presumably call via a tool. otaku-chat
  gives local models no shell/tool access at all (project-wide
  constraint — see README's "no shell tool is ever exposed to the
  model"), so there's no FastAPI, no server, no port. Instead: the model
  writes a plain fenced ```call block in its own chat reply
  (```call\\nfunc_name(arg=1)\\n```), app.py regexes that out of the
  reply after the turn completes, and faas.py resolves + safety-gates +
  runs it. No tool-calling capability required from the model at all —
  works on a 3B model that's never seen a function-calling schema.

- Every call runs in a subprocess (via faas_caller.py, a fixed
  package-shipped script — never the user's FUNCTIONS.py itself) so a
  crashing or hanging function can't take the TUI down, with a
  configurable wall-clock timeout (config.get_faas_call_timeout()).

- The model is aware of what's callable via render_functions_block()
  (same "inject a rendered block only when relevant" pattern as
  memory/facts/snippets/vault), built from AST-parsed signatures — never
  by executing FUNCTIONS.py just to introspect it.

- The model can PROPOSE a new/edited function (propose_function_edit)
  but can never write FUNCTIONS.py directly; app.py routes a proposal
  through a diff-preview + explicit user confirmation (faas_ui.py)
  before apply_function_edit() ever touches the file. Manually
  creating/deleting functions through the editor UI bypasses proposal
  entirely, same as /memory's Slate editor bypasses memory.py's curation
  side-call.

Safety posture: EVERY call — whether the model requested it via a
```call block or the user fires it manually from the options list —
goes through a Yes/No confirmation showing the exact function name and
args before it runs. No "always allow" bypass, no silent auto-execution.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config
from .fileio import locked_atomic_write
from .threats import scan_for_threats

_CALL_BLOCK_RE = None  # compiled lazily in find_call_requests to keep the import list free of `re` at module scope for callers that only need the dataclasses


DEFAULT_FUNCTIONS_TEMPLATE = '''"""User-defined functions, callable by the model via a ```call block in
its reply, or manually from /functions' options list.

Only plain functions with JSON-friendly (str/int/float/bool/list/dict/
None) parameters and return values are usable here — the caller
subprocess round-trips args and results through JSON, so anything else
(a file handle, a class instance, ...) won't survive the call.

The model NEVER writes to this file directly. It can propose new
functions or edits in chat, but the app always shows you the exact
before/after and asks for a decision first.
"""


def example_greeting(name: str) -> str:
    """A tiny example so /functions has something to show on first run."""
    return f"Hello, {name}! This function was called through otaku-chat's FaaS hands."
'''


@dataclass
class FunctionInfo:
    name: str
    params: list[str]  # "name" or "name: type" or "name=default", best-effort from the AST
    docstring: str


@dataclass
class CallRequest:
    """One ```call block parsed out of a model reply."""
    func_name: str
    kwargs: dict
    raw_block: str  # the original block text, shown to the user for transparency


class FaasError(Exception):
    pass


# --- functions file lifecycle -------------------------------------------

def functions_path() -> Path:
    return Path(config.get_functions_path())


def ensure_functions_file() -> Path:
    """Create FUNCTIONS.py from the template if it doesn't exist yet
    (first-run convenience, same pattern as memory.py's MEMORY.md)."""
    path = functions_path()
    if not path.exists():
        locked_atomic_write(path, DEFAULT_FUNCTIONS_TEMPLATE)
    return path


def read_functions_source() -> str:
    path = ensure_functions_file()
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- signature introspection (AST-only, never executes the file) --------

def list_functions() -> list[FunctionInfo]:
    """Parse FUNCTIONS.py with ast (never imported/executed here — only
    faas_caller.py's isolated subprocess ever actually runs user code)
    and return every top-level function's name/params/docstring."""
    source = read_functions_source()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    infos = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        params = []
        args = node.args
        defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
        for arg, default in zip(args.args, defaults):
            piece = arg.arg
            if arg.annotation is not None:
                try:
                    piece += f": {ast.unparse(arg.annotation)}"
                except Exception:
                    pass
            if default is not None:
                try:
                    piece += f" = {ast.unparse(default)}"
                except Exception:
                    pass
            params.append(piece)
        docstring = ast.get_docstring(node) or ""
        infos.append(FunctionInfo(name=node.name, params=params, docstring=docstring))
    return infos


def render_functions_block() -> str:
    """Render every available function's signature as a system-message
    block — injected every turn (unlike memory/facts/snippets/vault,
    this isn't relevance-gated: knowing what hands it has is baseline
    context for the model, not retrieval). Empty when FUNCTIONS.py has
    no callable functions yet."""
    infos = list_functions()
    if not infos:
        return ""
    lines = [
        "## Available functions (your \"hands\")",
        "You may call one of these by putting EXACTLY one fenced block in your reply:",
        "```call",
        "function_name(arg1=value1, arg2=value2)",
        "```",
        "The user will be shown the exact call and must approve it before it runs — you will "
        "see the result in a follow-up turn. Only call a function when it actually helps answer "
        "the user's request; never call one just to demonstrate you can.",
        "",
    ]
    for info in infos:
        sig = f"{info.name}({', '.join(info.params)})"
        lines.append(f"- {sig}" + (f" — {info.docstring.splitlines()[0]}" if info.docstring else ""))
    return "\n".join(lines)


# --- parsing a model's ```call block --------------------------------------

def find_call_requests(reply_text: str) -> list[CallRequest]:
    """Extract every ```call fenced block from a model's reply and parse
    it as a single Python-call-shaped expression: func_name(kw=val, ...).
    Deliberately narrow parsing (ast.parse of a synthetic module, not
    eval) — literal-only kwargs (str/int/float/bool/None/list/dict), no
    arbitrary expressions, so the model can't smuggle code execution
    through an argument value."""
    import re

    global _CALL_BLOCK_RE
    if _CALL_BLOCK_RE is None:
        _CALL_BLOCK_RE = re.compile(r"```call\s*\n(.*?)\n?```", re.DOTALL)

    requests = []
    for match in _CALL_BLOCK_RE.finditer(reply_text):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            call = _parse_call_expr(raw)
        except FaasError:
            continue  # malformed block — silently skip rather than crash a turn
        if call:
            requests.append(CallRequest(func_name=call[0], kwargs=call[1], raw_block=raw))
    return requests


def _parse_call_expr(text: str) -> tuple[str, dict] | None:
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise FaasError(f"could not parse call: {e}") from e

    if not isinstance(tree.body, ast.Call):
        raise FaasError("not a function call expression")
    call = tree.body
    if not isinstance(call.func, ast.Name):
        raise FaasError("call target must be a plain function name")
    if call.args:
        raise FaasError("positional arguments are not supported — use keyword arguments")

    kwargs = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise FaasError("**kwargs expansion is not supported")
        try:
            kwargs[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError) as e:
            raise FaasError(f"argument '{kw.arg}' must be a literal value: {e}") from e

    return call.func.id, kwargs


def parse_kwargs_string(func_name: str, args_text: str) -> dict:
    """Parse a manually-typed 'key=val, key2=val2' fragment (as typed into
    faas_ui.py's fire-args prompt) using the exact same literal-only
    parser as a model's ```call block, so a hand-fired call gets the same
    safety guarantee (no arbitrary expressions, just literal values).
    Raises FaasError on anything that doesn't parse as keyword-only
    literal args."""
    args_text = args_text.strip()
    _, kwargs = _parse_call_expr(f"{func_name}({args_text})") or (func_name, {})
    return kwargs


# --- invocation (isolated subprocess) -------------------------------------

def run_function(func_name: str, kwargs: dict, timeout: int | None = None) -> tuple[bool, object]:
    """Run one function in an isolated subprocess via faas_caller.py.
    Returns (ok, result_or_error_string). Never raises — a hung/crashing/
    hostile function always comes back as (False, "..."), never takes the
    caller down with it."""
    timeout = timeout if timeout is not None else config.get_faas_call_timeout()
    path = ensure_functions_file()

    try:
        json_kwargs = json.dumps(kwargs)
    except TypeError as e:
        return False, f"arguments are not JSON-serializable: {e}"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "otakuchat.faas_caller", str(path), func_name, json_kwargs],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"function '{func_name}' timed out after {timeout}s"
    except OSError as e:
        return False, f"could not launch caller subprocess: {e}"

    last_line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        payload = json.loads(last_line[0])
    except (json.JSONDecodeError, IndexError):
        stderr = (proc.stderr or "").strip()
        return False, f"caller produced no valid result (exit {proc.returncode}): {stderr[:500] or proc.stdout[:500]}"

    if payload.get("ok"):
        return True, payload.get("result")
    return False, payload.get("error", "unknown error")


# --- model-proposed function edits (never auto-applied) --------------------

def propose_function_edit(new_full_source: str) -> tuple[bool, str]:
    """Validate a model-proposed full replacement of FUNCTIONS.py before
    it's ever shown to the user for approval. Returns (ok, message) —
    ok=False means the proposal is rejected outright and never reaches a
    confirm dialog at all (syntax error, or a threat-scan hit on a body
    that looks like it's trying to smuggle an injection payload into a
    docstring/comment that gets replayed via render_functions_block()).
    Does NOT write anything — apply_function_edit() is the only writer,
    called only after explicit user approval."""
    try:
        ast.parse(new_full_source)
    except SyntaxError as e:
        return False, f"proposed code has a syntax error: {e}"

    if scan_for_threats(new_full_source):
        return False, "proposed code was flagged by the safety scanner and was not applied"

    return True, "proposal looks valid — awaiting user approval"


def apply_function_edit(new_full_source: str) -> None:
    """Write a user-approved function-file replacement. Callers MUST have
    already run this through propose_function_edit() and gotten ok=True,
    and gotten explicit user confirmation — this function itself performs
    no gating, matching Slate's own save-is-final contract for the other
    curated files."""
    locked_atomic_write(functions_path(), new_full_source)


def delete_function(func_name: str) -> tuple[bool, str]:
    """Remove one function definition from FUNCTIONS.py by name (used by
    faas_ui.py's manual delete action). Returns (ok, message)."""
    source = read_functions_source()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"FUNCTIONS.py currently has a syntax error, refusing to edit it: {e}"

    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target = node
            break
    if target is None:
        return False, f"no function named '{func_name}' found"

    lines = source.splitlines(keepends=True)
    start = target.lineno - 1
    end = target.end_lineno
    del lines[start:end]
    new_source = "".join(lines)
    apply_function_edit(new_source)
    return True, f"deleted '{func_name}'"
