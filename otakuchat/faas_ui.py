"""The FaaS "hands" UI — /functions' manual-fire options list, and the
Yes/No safety confirmation every call (model-requested or manually
fired) goes through before it actually runs.

FunctionCallConfirm is deliberately its own small ModalScreen rather
than reusing pickers.py's generic ConfirmDialog: it needs to render the
function name AND the exact kwargs as a readable multi-line block (a
bare ConfirmDialog only takes a single prompt string), and it's the one
gate this whole feature's safety story rests on, so keeping it a
distinct, easy-to-find class matters more than saving a few lines by
sharing ConfirmDialog.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen

from textual.widgets import Label

from . import faas
from .inputer import TextPrompt
from .pickers import ListPicker


class FunctionCallConfirm(ModalScreen[bool]):
    """Yes/No gate shown before EVERY function call executes — whether
    the model proposed it via a ```call block or the user fired it
    manually from FunctionsMenu. No 'always allow' bypass by design (see
    faas.py's module docstring): every single call gets a fresh, explicit
    decision."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(self, func_name: str, kwargs: dict, source: str = "model") -> None:
        super().__init__()
        self.func_name = func_name
        self.kwargs = kwargs
        self.source = source  # "model" | "manual" — shown so a manual fire and a model-requested one look distinct

    def compose(self) -> ComposeResult:
        args_repr = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        origin = "The model wants to call:" if self.source == "model" else "About to call:"
        with Container(id="faas-confirm-c1"):
            yield Label(origin, id="faas-confirm-l1")
            yield Label(f"{self.func_name}({args_repr})", id="faas-confirm-l2")
            yield Label("[y]es / [n]o / Esc", id="faas-confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class FunctionCallResult(ModalScreen[None]):
    """A dismissible result popup shown after a confirmed call finishes —
    separate from the confirm dialog so the user reads name+args, THEN
    (after committing to Yes) sees the outcome, rather than everything
    crammed into one screen."""

    BINDINGS = [("escape", "close", "Close"), ("enter", "close", "Close")]

    def __init__(self, func_name: str, ok: bool, result: object) -> None:
        super().__init__()
        self.func_name = func_name
        self.ok = ok
        self.result = result

    def compose(self) -> ComposeResult:
        status = "succeeded" if self.ok else "failed"
        with Container(id="faas-result-c1"):
            yield Label(f"'{self.func_name}' {status}", id="faas-result-l1")
            yield Label(str(self.result), id="faas-result-l2")
            yield Label("Enter/Esc to close", id="faas-result-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class FunctionArgsPrompt(TextPrompt):
    """TextPrompt subclass with function-specific placeholder text — used
    when the user fires a function manually from FunctionsMenu and it
    takes parameters (e.g. 'name="Otaku-chan"')."""


def build_functions_menu() -> ListPicker:
    """/functions — every callable function plus a fixed 'Manage
    FUNCTIONS.py' entry to jump straight to the Slate editor. Signature
    is shown in the label so the user knows what args a manual fire
    needs without opening the editor first."""
    infos = faas.list_functions()
    options = [
        (f"{info.name}({', '.join(info.params)})", info.name)
        for info in infos
    ]
    options.append(("✎ Manage FUNCTIONS.py (open in editor)", "__manage__"))
    return ListPicker(
        "Functions — Enter to fire, Esc to cancel",
        options,
        "No functions defined yet. Pick 'Manage FUNCTIONS.py' to add one.",
    )
