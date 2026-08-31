import json
import os
import shlex
import subprocess
import urllib.request
import re
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Label, Markdown, Static, TextArea, OptionList
from textual.widgets.option_list import Option

import config

# -------------------------------------------------------------------------
# Hermes Architecture: Tool Error Sanitization
# -------------------------------------------------------------------------
def _sanitize_tool_error(error_msg: str) -> str:
    """
    Strip structural framing tokens from a tool error before showing it to the model.
    Prevents framing escapes and prompt injection from arbitrary terminal output.
    """
    if not error_msg:
        return "[TOOL_ERROR] "
    # Strip XML role tags and markdown fences to prevent framing escapes
    sanitized = re.sub(r'</?(?:tool_call|function_call|result|response|output|input|system|assistant|user)>', "", error_msg, flags=re.IGNORECASE)
    sanitized = re.sub(r'^\s*```(?:json|xml|html|markdown)?\s*', "", sanitized, flags=re.MULTILINE)
    sanitized = re.sub(r'\s*```\s*$', "", sanitized, flags=re.MULTILINE)
    sanitized = re.sub(r'<!\[CDATA\[.*?\]\]>', "", sanitized, flags=re.DOTALL)
    
    # Cap the message at a sane upper bound before it becomes part of the conversation
    if len(sanitized) > 2000:
        sanitized = sanitized[:1997] + "..."
    return f"[TOOL_ERROR] {sanitized}"

# -------------------------------------------------------------------------
# 1. The Colonel - Macro Engine 
# -------------------------------------------------------------------------
STATE_FILE = "commands.json"

def load_macros():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_macro(title, executable):
    macros = load_macros()
    macros.append({"title": title, "executable": executable})
    with open(STATE_FILE, "w") as f:
        json.dump(macros, f, indent=4)

class ColonelMenu(Screen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Label("🎖️ THE COLONEL - Select a Macro to Execute", id="col-header")
        yield OptionList(id="macro-list")

    def on_mount(self) -> None:
        macro_list = self.query_one("#macro-list", OptionList)
        self.macros = load_macros()
        
        if not self.macros:
            macro_list.add_option(Option("No macros saved yet! Use /sh to generate some.", disabled=True))
            return
            
        for idx, macro in enumerate(self.macros):
            display_text = f"{macro['title']}  —  [ {macro['executable']} ]"
            macro_list.add_option(Option(display_text, id=str(idx)))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self.macros:
            return
            
        idx = int(event.option.id)
        selected_macro = self.macros[idx]
        command = selected_macro["executable"]
        
        with self.app.suspend():
            print(f"\n--- Running: {selected_macro['title']} ---")
            subprocess.run(command, shell=True)
            input("\n[Press Enter to return to OtakuChat]")
            
        self.dismiss(f"Executed: {selected_macro['title']}")

    def action_cancel(self) -> None:
        self.dismiss(None)

# -------------------------------------------------------------------------
# 2. Custom Long-Prompt Editor Components
# -------------------------------------------------------------------------
class PromptTextArea(TextArea):
    def _on_key(self, event: events.Key) -> None:
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        if event.character in pairs:
            event.prevent_default()
            closing = pairs[event.character]
            self.insert(f"{event.character}{closing}")
            self.move_cursor_relative(columns=-1)

class PromptEditor(Screen):
    BINDINGS = [
        ("f9", "submit_prompt", "Submit Prompt"),
        ("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold cyan]F9[/bold cyan] Submit Prompt │ [bold cyan]Esc[/bold cyan] Cancel",
            id="prompt-cheat-sheet"
        )
        yield PromptTextArea(language="markdown", id="prompt-editor-area")

    def on_mount(self) -> None:
        self.query_one(PromptTextArea).focus()

    def action_submit_prompt(self) -> None:
        text = self.query_one(PromptTextArea).text.strip()
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)

# -------------------------------------------------------------------------
# 3. Main Chat Application
# -------------------------------------------------------------------------
class OtakuChat(App):
    CSS_PATH = "master.tcss"
    MACRO_PATTERN = re.compile(r"alias:\s*(.*?)\s+command:\s*(.*?)(?=\n|$)", re.IGNORECASE)
    
    # Define capabilities for the agent
    AGENT_TOOLS = [{
        "type": "function",
        "function": {
            "name": "execute_terminal_command",
            "description": "Execute a bash/shell command on the local system and return the output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute"}
                },
                "required": ["command"]
            }
        }
    }]

    def __init__(self):
        super().__init__()
        
        self.model = config.get_model()
        self.api_url = config.get_api_url()
        self.soul_file = config.get_soul_path()
        self.memory_file = config.get_memory_path()
        
        try:
            with open(self.soul_file, "r") as f:
                soul_text = f.read()
        except FileNotFoundError:
            soul_text = "You are a helpful elite AI assistant."
            
        try:
            with open(self.memory_file, "r") as f:
                memory_text = f.read()
        except FileNotFoundError:
            memory_text = ""
            
        system_prompt = f"{soul_text}\n\n{memory_text}"

        self.messages = [{"role": "system", "content": system_prompt}]
        self.conversation_history = f"*OtakuChat active. Model: {self.model}*"
        self.active_stream = ""
        self.MAX_CONTEXT_TURNS = 12 # Trigger trajectory compression threshold

    def compose(self) -> ComposeResult:
        yield Label("OtakuChat", id="main-l1")
        yield Markdown(self.conversation_history, id="chat-output")
        yield Input(placeholder=">>> ( /prompt | /sh <cmd> | /add <file> | /col )", id="main-i1")

    def append_to_ui(self, text: str) -> None:
        self.conversation_history += text
        self.query_one("#chat-output", Markdown).update(self.conversation_history)
        
    def stream_to_ui(self, chunk: str) -> None:
        self.active_stream += chunk
        self.query_one("#chat-output", Markdown).update(self.conversation_history + self.active_stream)

    @work(thread=True)
    def compress_trajectory_bg(self) -> None:
        """
        Compress agent trajectories to fit within a target token budget.
        Protects head turns (system, first interaction) and tail turns, 
        and replaces the middle compressible region with a single summary.
        """
        if len(self.messages) < self.MAX_CONTEXT_TURNS:
            return
            
        self.call_from_thread(self.append_to_ui, "\n\n*[System: Context limit reached. Compressing trajectory...]*")
        
        # 1. Protect head and tail
        protected_head = self.messages[:2] 
        protected_tail = self.messages[-4:]
        compressible_region = self.messages[2:-4]
        
        # 2. Extract content for summary
        content_to_summarize = ""
        for i, turn in enumerate(compressible_region):
            role = turn.get("role", "unknown")
            val = turn.get("content", "")
            if len(val) > 1000:
                val = val[:500] + "\n...[truncated]...\n" + val[-500:]
            content_to_summarize += f"[Turn {i} - {role.upper()}]:\n{val}\n\n"
            
        prompt = (
            "Summarize the following agent conversation turns concisely. "
            "Write the summary from a neutral perspective describing what the assistant did and learned. "
            "Include actions taken, key information obtained, and relevant outputs.\n"
            "Write only the summary, starting with '[CONTEXT SUMMARY]:' prefix.\n\n"
            f"--- TURNS TO SUMMARIZE ---\n{content_to_summarize}"
        )
        
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.api_url, data=data, headers={"Content-Type": "application/json"})
        
        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                summary = result["message"]["content"].strip()
                if not summary.startswith("[CONTEXT SUMMARY]:"):
                    summary = f"[CONTEXT SUMMARY]: {summary}"
                
                # 3. Replace compressed region with a single human summary message
                self.messages = protected_head + [{"role": "user", "content": summary}] + protected_tail
                self.call_from_thread(self.append_to_ui, "\n*[System: Trajectory successfully compressed.]*")
        except Exception as e:
            self.call_from_thread(self.append_to_ui, f"\n*[System: Trajectory compression failed: {e}]*")

    @work(thread=True)
    def query_ollama_agent_loop(self) -> None:
        """
        The core agent reasoning loop: prompt assembly -> inference -> tool selection -> execution -> environment feedback loop.
        """
        payload = {
            "model": self.model, 
            "messages": self.messages, 
            "stream": False,
            "tools": self.AGENT_TOOLS
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.api_url, data=data, headers={"Content-Type": "application/json"})

        self.active_stream = "\n\n**Assistant:**\n"
        self.call_from_thread(self.stream_to_ui, "*(Thinking...)*")

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                message_data = result.get("message", {})
                
                self.active_stream = "\n\n**Assistant:**\n" # Clear the "Thinking" indicator
                
                # Handle standard text response
                if message_data.get("content"):
                    content = message_data["content"]
                    self.messages.append({"role": "assistant", "content": content})
                    self.call_from_thread(self.append_to_ui, self.active_stream + content)
                    self.active_stream = ""
                
                # Handle Tool Execution
                if "tool_calls" in message_data and message_data["tool_calls"]:
                    # Append the tool call intent to history
                    self.messages.append(message_data)
                    
                    for tool_call in message_data["tool_calls"]:
                        func_name = tool_call["function"]["name"]
                        func_args = tool_call["function"]["arguments"]
                        
                        if func_name == "execute_terminal_command":
                            command = func_args.get("command", "")
                            self.call_from_thread(self.append_to_ui, f"\n\n*[Agent executing tool: `execute_terminal_command`]*\n```bash\n{command}\n```\n")
                            
                            try:
                                # Execute code + capture output safely
                                process = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
                                tool_result = process.stdout if process.stdout else process.stderr
                                if not tool_result:
                                    tool_result = "[Command executed successfully with no output]"
                            except subprocess.TimeoutExpired:
                                tool_result = _sanitize_tool_error("Error: Command execution timed out after 30 seconds.")
                            except Exception as e:
                                tool_result = _sanitize_tool_error(f"Error executing command: {str(e)}")
                                
                            # Cap extremely long terminal outputs
                            if len(tool_result) > 2000:
                                tool_result = tool_result[:1997] + "..."
                                
                            self.messages.append({
                                "role": "tool",
                                "name": func_name,
                                "content": tool_result
                            })
                            
                    # Trigger the Autonomous Loop (Re-query after tool execution)
                    self.query_ollama_agent_loop()
                    return

            self.compress_trajectory_bg()
            
        except Exception as e:
            self.call_from_thread(self.append_to_ui, f"\n\n*Error connecting to Ollama: {e}*")
            self.active_stream = ""

    @work(thread=True)
    def generate_macro_bg(self, task: str) -> None:
        strict_prompt = (
            "CRITICAL: You are an elite system administration terminal copilot. "
            "You must ONLY output command blocks using this exact syntax:\n"
            "alias: [name]\ncommand: [linux command]\n"
            "Do NOT include markdown code ticks or explanations."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": strict_prompt},
                {"role": "user", "content": task}
            ],
            "stream": False
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.api_url, data=data, headers={"Content-Type": "application/json"})
        
        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                ai_output = result["message"]["content"].strip()
                
            match = self.MACRO_PATTERN.search(ai_output)
            if match:
                title = match.group(1).strip()
                executable = match.group(2).strip()
                save_macro(title, executable)
                msg = (
                    f"\n\n*System: Pumped '{title}' into The Colonel!*\n"
                    f"```bash\n{executable}\n```"
                )
            else:
                msg = f"\n\n*System: Failed to parse AI output into macro format:*\n{ai_output}"
                
            self.call_from_thread(self.append_to_ui, msg)
        except Exception as e:
            self.call_from_thread(self.append_to_ui, f"\n\n*System: Macro generation failed: {e}*")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value
        event.input.value = "" 
        if not user_input.strip():
            return
            
        cmd_lower = user_input.strip().lower()

        if cmd_lower == "/config":
            with self.suspend():
                subprocess.run(shlex.split(config.get_editor()) + [str(config.CONFIG_FILE)])
            self.append_to_ui("\n\n*System: Closed config editor. Restart app to apply model changes.*")
            return
            
        elif cmd_lower == "/soul":
            with self.suspend():
                subprocess.run(shlex.split(config.get_editor()) + [self.soul_file])
            self.append_to_ui("\n\n*System: Closed SOUL.md editor. Restart app to apply identity changes.*")
            return

        elif cmd_lower in ["/quit", "/exit"]:
            self.exit()
            return

        elif cmd_lower == "/prompt":
            self.push_screen(PromptEditor(), self.handle_long_prompt)
            return

        elif cmd_lower == "/col":
            self.push_screen(ColonelMenu(), self.handle_macro_result)
            return

        elif cmd_lower.startswith("/add "):
            filepath = user_input[5:].strip()
            path = Path(filepath)
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    file_context = f"\n# Attached File: {path.name}\n```\n{content}\n```\n"
                    self.messages[0]["content"] += file_context
                    self.append_to_ui(f"\n\n*System: Injected {path.name} into system context.*")
                except Exception as e:
                    self.append_to_ui(f"\n\n*System: Failed to read {filepath} - {e}*")
            else:
                self.append_to_ui(f"\n\n*System: File not found: {filepath}*")
            return

        elif cmd_lower.startswith("/sh "):
            task = user_input[4:].strip()
            self.append_to_ui(f"\n\n**You (Macro Task):** {task}\n*Generating macro...*")
            self.generate_macro_bg(task)
            return

        self.process_chat_message(user_input)

    def handle_long_prompt(self, long_text: str | None) -> None:
        if long_text:
            self.process_chat_message(long_text)
            
    def handle_macro_result(self, result: str | None) -> None:
        if result:
            self.append_to_ui(f"\n\n*System: {result}*")

    def process_chat_message(self, user_input: str) -> None:
        self.messages.append({"role": "user", "content": user_input})
        self.append_to_ui(f"\n\n**You:**\n{user_input}")
        
        # Fire background worker to handle the agent loop and tool routing
        self.query_ollama_agent_loop()

if __name__ == "__main__":
    app = OtakuChat()
    app.run()
