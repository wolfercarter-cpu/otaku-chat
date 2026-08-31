from textual.screen import Screen
from textual.widgets import Static, TextArea
from textual import events

class PromptTextArea(TextArea):
    """Lightweight TextArea that keeps your auto-closing brackets."""
    def _on_key(self, event: events.Key) -> None:
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        if event.character in pairs:
            event.prevent_default()
            closing = pairs[event.character]
            self.insert(f"{event.character}{closing}")
            self.move_cursor_relative(columns=-1)

class PromptEditor(Screen):
    """A full-screen modal for typing long prompts."""
    
    BINDINGS = [
        ("f9", "submit_prompt", "Submit Prompt"),
        ("escape", "cancel", "Cancel"),
    ]

    def compose(self):
        yield Static(
            "[bold cyan]F9[/bold cyan] Submit Prompt │ "
            "[bold cyan]Esc[/bold cyan] Cancel",
            id="prompt-cheat-sheet"
        )
        # We use markdown language so syntax highlights naturally
        yield PromptTextArea(language="markdown", id="prompt-editor-area")

    def on_mount(self) -> None:
        self.query_one(PromptTextArea).focus()

    def action_submit_prompt(self) -> None:
        """Grabs the text and returns it to the main app."""
        text = self.query_one(PromptTextArea).text.strip()
        self.dismiss(text)  # Closes screen and passes text back

    def action_cancel(self) -> None:
        """Closes without sending."""
        self.dismiss(None)
