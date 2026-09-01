"""A generic dropdown-autocomplete component for Input/TextArea targets.

Ported from Otakumafia's gmag project (gmag/autocomplete_engine.py), with
its vendored FuzzySearch class dropped in favor of textual.fuzzy.FuzzySearch
(same algorithm, already ships with Textual — see pickers.py for the same
dedup done there). Everything else — the dropdown positioning, the
Input.Changed/Key event plumbing, prevent-default handling for Enter/Tab
so a target doesn't insert a literal character when accepting a
suggestion — is otherwise as gmag built it.

editor.py's CodeAutoComplete subclasses AutoComplete to target a 2D
TextArea (word-completion from the document's own text) instead of the
1D Input this base class assumes by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter
from typing import Callable, ClassVar, Sequence, cast

from rich.text import Text
from textual import events, on
from textual.binding import Binding
from textual.content import Content
from textual.css.query import NoMatches
from textual.fuzzy import FuzzySearch
from textual.geometry import Offset, Region, Spacing
from textual.style import Style
from textual.widget import Widget
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


@dataclass
class TargetState:
    text: str
    cursor_position: int


class DropdownItem(Option):
    def __init__(
        self,
        main: str | Content,
        prefix: str | Content | None = None,
        id: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.main = Content(main) if isinstance(main, str) else main
        self.prefix = Content(prefix) if isinstance(prefix, str) else prefix
        left = self.prefix
        prompt = self.main
        if left:
            prompt = Content.assemble(left, self.main)
        super().__init__(prompt, id, disabled)

    @property
    def value(self) -> str:
        return self.main.plain


class DropdownItemHit(DropdownItem):
    pass


class AutoCompleteList(OptionList):
    pass


class AutoComplete(Widget):
    BINDINGS = [
        Binding("escape", "hide", "Hide dropdown", show=False),
    ]

    DEFAULT_CSS = """\
    AutoComplete {
        height: auto;
        width: auto;
        max-height: 12;
        display: none;
        background: $surface;
        overlay: screen;

        & AutoCompleteList {
            width: auto;
            height: auto;
            border: none;
            padding: 0;
            margin: 0;
            scrollbar-size-vertical: 1;
            text-wrap: nowrap;
            color: $foreground;
            background: transparent;
        }

        & .autocomplete--highlight-match {
            text-style: bold;
        }
    }
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "autocomplete--highlight-match",
    }

    def __init__(
        self,
        target: Input | str,
        candidates: Sequence[DropdownItem | str] | Callable[[TargetState], list[DropdownItem]] | None = None,
        *,
        prevent_default_enter: bool = True,
        prevent_default_tab: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._target = target

        if isinstance(candidates, Sequence):
            self.candidates = [
                candidate if isinstance(candidate, DropdownItem) else DropdownItem(main=candidate)
                for candidate in candidates
            ]
        else:
            self.candidates = candidates

        self.prevent_default_enter = prevent_default_enter
        self.prevent_default_tab = prevent_default_tab
        self._target_state = TargetState("", 0)
        self._fuzzy_search = FuzzySearch()
        self._previous_terminal_cursor_position = (0, 0)

    def compose(self):
        option_list = AutoCompleteList()
        option_list.can_focus = False
        yield option_list

    def on_mount(self) -> None:
        self.target.message_signal.subscribe(self, self._listen_to_messages)
        self._subscribe_to_target()
        self._handle_target_update()

        def _realign(_=None) -> None:
            if self.is_attached and self._previous_terminal_cursor_position != self.app.cursor_position:
                self._align_to_target()
                self._previous_terminal_cursor_position = self.app.cursor_position

        self.screen.screen_layout_refresh_signal.subscribe(self, _realign)

    def _listen_to_messages(self, event: events.Event) -> None:
        try:
            option_list = self.option_list
        except NoMatches:
            return

        if isinstance(event, events.Key) and option_list.option_count and not event._no_default_action:
            self._handle_key(event)

        if isinstance(event, Input.Changed):
            self._handle_target_update()

    def _handle_key(self, event: events.Key) -> None:
        """Applies a dropdown-relevant keypress, calling prevent_default()/stop()
        on it when the dropdown consumes it.

        Exposed separately from _listen_to_messages so a target that performs
        its own default action on a key (like TextArea inserting a newline on
        Enter) can call this *before* that default action runs, instead of
        relying on the message-signal notification, which only fires after
        the target has already processed the key.
        """
        option_list = self.option_list
        if not option_list.option_count:
            return

        displayed = self.display
        highlighted = option_list.highlighted or 0
        if event.key == "down":
            if option_list.option_count == 1:
                search_string = self.get_search_string(self._get_target_state())
                first_option = option_list.get_option_at_index(0).prompt
                text_from_option = first_option.plain if isinstance(first_option, Text) else first_option
                if text_from_option == search_string:
                    return
            event.prevent_default()
            event.stop()
            if displayed:
                highlighted = (highlighted + 1) % option_list.option_count
            else:
                self.display = True
                highlighted = 0
            option_list.highlighted = highlighted

        elif event.key == "up":
            if displayed:
                event.prevent_default()
                event.stop()
                highlighted = (highlighted - 1) % option_list.option_count
                option_list.highlighted = highlighted
        elif event.key == "enter":
            if self.prevent_default_enter and displayed:
                event.prevent_default()
                event.stop()
            self._complete(option_index=highlighted)
        elif event.key == "tab":
            if self.prevent_default_tab and displayed:
                event.prevent_default()
                event.stop()
            self._complete(option_index=highlighted)
        elif event.key == "escape":
            if displayed:
                event.prevent_default()
                event.stop()
            self.action_hide()

    def action_hide(self) -> None:
        self.styles.display = "none"

    def action_show(self) -> None:
        self.styles.display = "block"

    def _complete(self, option_index: int) -> None:
        if not self.display or self.option_list.option_count == 0:
            return
        option_list = self.option_list
        highlighted = option_index
        option = cast(DropdownItem, option_list.get_option_at_index(highlighted))
        highlighted_value = option.value
        with self.prevent(Input.Changed):
            self.apply_completion(highlighted_value, self._get_target_state())
        self.post_completion()

    def post_completion(self) -> None:
        self.action_hide()

    def apply_completion(self, value: str, state: TargetState) -> None:
        target = self.target
        target.value = ""
        target.insert_text_at_cursor(value)
        new_target_state = self._get_target_state()
        self._rebuild_options(new_target_state, self.get_search_string(new_target_state))

    @property
    def target(self) -> Input:
        if isinstance(self._target, Input):
            return self._target
        else:
            target = self.screen.query_one(self._target)
            assert isinstance(target, Input)
            return target

    def _subscribe_to_target(self) -> None:
        target = self.target
        self.watch(target, "has_focus", self._handle_focus_change)
        self.watch(target, "selection", self._align_and_rebuild)

    def _align_and_rebuild(self) -> None:
        self._align_to_target()
        self._target_state = self._get_target_state()
        search_string = self.get_search_string(self._target_state)
        self._rebuild_options(self._target_state, search_string)

    def _align_to_target(self) -> None:
        x, y = self.target.cursor_screen_offset
        dropdown = self.option_list
        width, height = dropdown.outer_size
        x, y, _width, _height = Region(x - 1, y + 1, width, height).constrain(
            "inside", "none", Spacing.all(0), self.screen.scrollable_content_region,
        )
        self.absolute_offset = Offset(x, y)

    def _get_target_state(self) -> TargetState:
        target = self.target
        return TargetState(text=target.value, cursor_position=target.cursor_position)

    def _handle_focus_change(self, has_focus: bool) -> None:
        if not has_focus:
            self.action_hide()
        else:
            target_state = self._get_target_state()
            search_string = self.get_search_string(target_state)
            self._rebuild_options(target_state, search_string)

    def _handle_target_update(self) -> None:
        self._target_state = self._get_target_state()
        search_string = self.get_search_string(self._target_state)
        self._rebuild_options(self._target_state, search_string)
        self._align_to_target()
        if self.should_show_dropdown(search_string):
            self.action_show()
        else:
            self.action_hide()

    def should_show_dropdown(self, search_string: str) -> bool:
        option_list = self.option_list
        option_count = option_list.option_count
        if len(search_string) == 0 or option_count == 0:
            return False
        elif option_count == 1:
            first_option = option_list.get_option_at_index(0).prompt
            text_from_option = first_option.plain if isinstance(first_option, Text) else first_option
            return text_from_option != search_string
        else:
            return True

    def _rebuild_options(self, target_state: TargetState, search_string: str) -> None:
        option_list = self.option_list
        option_list.clear_options()
        if self.target.has_focus:
            matches = self._compute_matches(target_state, search_string)
            if matches:
                option_list.add_options(matches)
                option_list.highlighted = 0

    def get_search_string(self, target_state: TargetState) -> str:
        return target_state.text[: target_state.cursor_position]

    def _compute_matches(self, target_state: TargetState, search_string: str) -> list[DropdownItem]:
        candidates = self.get_candidates(target_state)
        matches = self.get_matches(target_state, candidates, search_string)
        return matches

    def get_candidates(self, target_state: TargetState) -> list[DropdownItem]:
        candidates = self.candidates
        if isinstance(candidates, Sequence):
            return list(candidates)
        elif candidates is None:
            raise NotImplementedError("You must implement get_candidates")
        else:
            return candidates(target_state)

    def get_matches(self, target_state: TargetState, candidates: list[DropdownItem], search_string: str) -> list[DropdownItem]:
        if not search_string:
            return candidates
        matches_and_scores: list[tuple[DropdownItem, float]] = []
        append_score = matches_and_scores.append
        match = self.match

        for candidate in candidates:
            candidate_string = candidate.value
            score, offsets = match(search_string, candidate_string)
            if score > 0:
                highlighted = self.apply_highlights(candidate.main, offsets)
                highlighted_item = DropdownItemHit(
                    main=highlighted, prefix=candidate.prefix, id=candidate.id, disabled=candidate.disabled,
                )
                append_score((highlighted_item, score))

        matches_and_scores.sort(key=itemgetter(1), reverse=True)
        matches = [match for match, _ in matches_and_scores]
        return matches

    def match(self, query: str, candidate: str) -> tuple[float, Sequence[int]]:
        return self._fuzzy_search.match(query, candidate)

    def apply_highlights(self, candidate: Content, offsets: Sequence[int]) -> Content:
        match_style = Style.from_rich_style(self.get_component_rich_style("autocomplete--highlight-match", partial=True))
        plain = candidate.plain
        for offset in offsets:
            if not plain[offset].isspace():
                candidate = candidate.stylize(match_style, offset, offset + 1)
        return candidate

    @property
    def option_list(self) -> AutoCompleteList:
        return self.query_one(AutoCompleteList)

    @on(OptionList.OptionSelected, "AutoCompleteList")
    def _apply_completion(self, event: OptionList.OptionSelected) -> None:
        self._complete(event.option_index)
