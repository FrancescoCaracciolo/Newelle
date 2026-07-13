"""ModeEditorDialog — create or edit a Mode.

An ``Adw.PreferencesDialog`` (same base class as ``ProfileDialog``) used both
for creating a new Mode and editing an existing one. Lets the user pick a name,
an icon, a description, the mode prompt (injected via ``{MODEPROMPT}``), and a
3-state override (No change / Enable / Remove) for each tool and skill.
"""

import gettext

from gi.repository import Gtk, Adw

from ...modes import DEFAULT_MODE_NAME, DEFAULT_MODE_ICON, NO_CHANGE, ENABLE, REMOVE
from .multiline import MultilineEntry

_ = gettext.gettext

# Curated set of symbolic icons offered in the icon picker. All are standard
# GTK/libadwaita symbolics that ship with the icon theme.
MODE_ICON_CHOICES = [
    "user-available-symbolic",
    "document-edit-symbolic",
    "brain-augemnted-symbolic",
    "system-search-symbolic",
    "code-symbolic",
    "applications-science-symbolic",
    "chat-symbolic",
    "emoji-objects-symbolic",
    "lightbulb-symbolic",
    "system-run-symbolic",
    DEFAULT_MODE_ICON,
]


class ModeEditorDialog(Adw.PreferencesDialog):
    """Dialog to create or edit a Mode."""

    def __init__(self, controller, window, mode_name: str | None = None):
        super().__init__()
        self.controller = controller
        self.window = window
        self.mode_manager = controller.mode_manager

        self.editing = mode_name is not None
        self.original_name = mode_name
        self.is_builtin = mode_name == DEFAULT_MODE_NAME

        if self.editing:
            self.set_title(_("Edit Mode"))
        else:
            self.set_title(_("New Mode"))
        self.set_search_enabled(False)

        self.page = Adw.PreferencesPage()
        self.add(self.page)

        # Local working copy of the mode being edited.
        existing = self.mode_manager.get_mode(mode_name) if self.editing else None
        self._working = {
            "name": mode_name or "",
            "prompt": (existing or {}).get("prompt", ""),
            "description": (existing or {}).get("description", ""),
            "icon": (existing or {}).get("icon", DEFAULT_MODE_ICON),
            "tools": dict((existing or {}).get("tools", {})),
            "skills": dict((existing or {}).get("skills", {})),
        }

        self._build_identity_group()
        self._build_prompt_group()
        self._build_tools_group()
        self._build_skills_group()
        self._build_actions_group()

    # ------------------------------------------------------------------ #
    # Identity: name / icon / description
    # ------------------------------------------------------------------ #
    def _build_identity_group(self):
        group = Adw.PreferencesGroup(title=_("Mode"))
        self.page.add(group)

        # Name
        name_row = Adw.EntryRow(title=_("Name"), text=self._working["name"])
        if self.is_builtin:
            name_row.set_editable(False)
        else:
            name_row.connect("changed", self._on_name_changed)
        self.name_row = name_row
        group.add(name_row)

        # Description
        desc_row = Adw.EntryRow(
            title=_("Description"), text=self._working["description"]
        )
        desc_row.connect("changed", self._on_desc_changed)
        group.add(desc_row)

        # Icon picker: horizontal flow of toggle buttons acting as a radio
        # group (GTK handles mutual exclusion natively via set_group).
        icon_row = Adw.ActionRow(title=_("Icon"))
        self.icon_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END
        )
        self.icon_buttons = []
        group_leader = None
        for icon_name in MODE_ICON_CHOICES:
            btn = Gtk.ToggleButton(
                css_classes=["flat"],
                tooltip_text=icon_name,
            )
            img = Gtk.Image(icon_name=icon_name, pixel_size=18)
            btn.set_child(img)
            if group_leader is None:
                group_leader = btn
            else:
                btn.set_group(group_leader)
            hid = btn.connect("toggled", self._on_icon_toggled, icon_name)
            btn._toggled_hid = hid
            self.icon_buttons.append(btn)
            self.icon_box.append(btn)
        icon_row.add_suffix(self.icon_box)
        group.add(icon_row)
        self._icon_group_leader = group_leader
        self._sync_icon_toggles()

    # ------------------------------------------------------------------ #
    # Prompt
    # ------------------------------------------------------------------ #
    def _build_prompt_group(self):
        group = Adw.PreferencesGroup(
            title=_("Mode Prompt"),
            description=_("Injected into the conversation via {MODEPROMPT}"),
        )
        self.page.add(group)

        row = Adw.PreferencesRow(
            title=_("Mode Prompt"), activatable=False, focusable=False
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        self.prompt_entry = MultilineEntry()
        self.prompt_entry.set_placeholder(_("Describe how the assistant should behave in this mode…"))
        self.prompt_entry.set_text(self._working["prompt"])
        self.prompt_entry.set_on_change(self._on_prompt_changed)
        box.append(self.prompt_entry)
        row.set_child(box)
        group.add(row)

    # ------------------------------------------------------------------ #
    # Tools / skills 3-state overrides
    # ------------------------------------------------------------------ #
    def _build_tools_group(self):
        group = Adw.PreferencesGroup(title=_("Tools"))
        self.page.add(group)
        tools = self.controller.tools.get_all_tools()
        if not tools:
            group.add(Adw.ActionRow(title=_("No tools available"), subtitle=""))
            return
        for tool in tools:
            group.add(
                self._build_state_row(
                    title=tool.title,
                    subtitle=tool.description,
                    icon_name=tool.icon_name or "tools-symbolic",
                    key=tool.name,
                    current=self._working["tools"].get(tool.name, NO_CHANGE),
                    target="tools",
                )
            )

    def _build_skills_group(self):
        group = Adw.PreferencesGroup(title=_("Skills"))
        self.page.add(group)
        skills = []
        if hasattr(self.controller, "skill_manager"):
            skills = list(self.controller.skill_manager.skills.values())
        if not skills:
            group.add(Adw.ActionRow(title=_("No skills available"), subtitle=""))
            return
        for skill in skills:
            group.add(
                self._build_state_row(
                    title=skill.name,
                    subtitle=skill.description,
                    icon_name="emoji-actions-symbolic",
                    key=skill.name,
                    current=self._working["skills"].get(skill.name, NO_CHANGE),
                    target="skills",
                )
            )

    def _build_state_row(self, title, subtitle, icon_name, key, current, target):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.add_prefix(Gtk.Image(icon_name=icon_name))

        # Segmented three-state control: Enable (✓, green) / No change (—, neutral)
        # / Remove (✗, red). Order is No change | Enable | Remove so the neutral
        # default sits on the left. Adw.Toggle has no CSS-class API, so the
        # color is carried by a Gtk.Image child with a semantic style class.
        group = Adw.ToggleGroup()
        group.add_css_class("state-toggle-group")
        group.set_valign(Gtk.Align.CENTER)
        for value, label, icon_name_t, style_class in (
            (NO_CHANGE, _("No change"), "go-jump-symbolic", "dim-label"),
            (ENABLE, _("Enable"), "object-select-symbolic", "success"),
            (REMOVE, _("Remove"), "user-trash-symbolic", "error"),
        ):
            toggle = Adw.Toggle()
            toggle.set_name(value)
            toggle.set_tooltip(label)
            icon = Gtk.Image(icon_name=icon_name_t)
            icon.add_css_class(style_class)
            toggle.set_child(icon)
            group.add(toggle)
        group.set_active_name(current if current else NO_CHANGE)
        group.connect("notify::active-name", self._on_state_selected, target, key)
        row.add_suffix(group)
        return row

    # ------------------------------------------------------------------ #
    # Actions: save / delete
    # ------------------------------------------------------------------ #
    def _build_actions_group(self):
        group = Adw.PreferencesGroup()
        self.page.add(group)

        save_btn = Gtk.Button(
            label=_("Save"), css_classes=["suggested-action"], hexpand=True
        )
        save_btn.connect("clicked", self._on_save)
        save_row = Adw.ActionRow(activatable=False)
        save_row.add_suffix(save_btn)
        group.add(save_row)

        if self.editing and not self.is_builtin:
            delete_btn = Gtk.Button(
                label=_("Delete Mode"),
                css_classes=["destructive-action"],
                hexpand=True,
            )
            delete_btn.connect("clicked", self._on_delete)
            delete_row = Adw.ActionRow(activatable=False)
            delete_row.add_suffix(delete_btn)
            group.add(delete_row)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _on_name_changed(self, row):
        self._working["name"] = row.get_text().strip()

    def _on_desc_changed(self, row):
        self._working["description"] = row.get_text()

    def _on_prompt_changed(self, _entry):
        self._working["prompt"] = self.prompt_entry.get_text()

    def _on_icon_toggled(self, btn, icon_name):
        # The toggle group handles mutual exclusion; we only record the choice
        # when a button becomes active.
        if btn.get_active():
            self._working["icon"] = icon_name

    def _on_state_selected(self, toggle_group, _pspec, target, key):
        value = toggle_group.props.active_name
        self._working[target][key] = value

    def _sync_icon_toggles(self):
        current = self._working["icon"] or DEFAULT_MODE_ICON
        for btn, icon_name in zip(self.icon_buttons, MODE_ICON_CHOICES):
            # Block our handler while programmatically setting the active state
            # so reflecting the initial selection doesn't recurse.
            handler_id = getattr(btn, "_toggled_hid", None)
            if handler_id is not None:
                btn.handler_block(handler_id)
            btn.set_active(icon_name == current)
            if handler_id is not None:
                btn.handler_unblock(handler_id)

    # ------------------------------------------------------------------ #
    # Save / delete
    # ------------------------------------------------------------------ #
    def _resolve_name(self) -> str | None:
        name = self._working["name"]
        if not name:
            return None
        if self.is_builtin:
            return DEFAULT_MODE_NAME
        if not self.editing and name in self.mode_manager.get_modes():
            return None  # name collision on create
        return name

    def _on_save(self, _button):
        name = self._resolve_name()
        if name is None:
            self.name_row.add_css_class("error")
            return
        # Drop neutral ("no_change") entries — they carry no information.
        tools = {k: v for k, v in self._working["tools"].items() if v != NO_CHANGE}
        skills = {k: v for k, v in self._working["skills"].items() if v != NO_CHANGE}

        if self.editing and (self.is_builtin or name == self.original_name):
            # Same-name edit (or the non-deletable builtin): update in place.
            self.mode_manager.update_mode(
                self.original_name,
                prompt=self._working["prompt"],
                description=self._working["description"],
                icon=self._working["icon"],
                tools=tools,
                skills=skills,
            )
        elif self.editing:
            # Rename: create under the new name, preserve active state, drop old.
            was_active = (
                self.mode_manager.get_active_mode_name() == self.original_name
            )
            self.mode_manager.create_mode(
                name,
                prompt=self._working["prompt"],
                description=self._working["description"],
                icon=self._working["icon"],
                tools=tools,
                skills=skills,
            )
            if was_active:
                # delete_mode() would reset active to Normal, so switch first.
                self.mode_manager.set_active_mode(name)
            self.mode_manager.delete_mode(self.original_name)
        else:
            self.mode_manager.create_mode(
                name,
                prompt=self._working["prompt"],
                description=self._working["description"],
                icon=self._working["icon"],
                tools=tools,
                skills=skills,
            )
            self.mode_manager.create_mode(
                name,
                prompt=self._working["prompt"],
                description=self._working["description"],
                icon=self._working["icon"],
                tools=tools,
                skills=skills,
            )

        # Propagate + reload so {MODEPROMPT} and tool visibility update.
        active = self.mode_manager.get_active_mode()
        self.controller.skill_manager.set_mode_overrides(active.get("skills", {}))
        self.controller.update_settings()
        self.window.refresh_mode_buttons()
        self.close()

    def _on_delete(self, _button):
        if self.is_builtin or not self.editing:
            return
        self.mode_manager.delete_mode(self.original_name)
        active = self.mode_manager.get_active_mode()
        self.controller.skill_manager.set_mode_overrides(active.get("skills", {}))
        self.controller.update_settings()
        self.window.refresh_mode_buttons()
        self.close()
