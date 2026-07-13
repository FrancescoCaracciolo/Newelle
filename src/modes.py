"""Modes infrastructure.

A **Mode** is a named overlay that customizes the assistant's behavior without
touching the active profile. It is composed of:

- ``prompt``  : free-form text injected into the system prompts via the
  ``{MODEPROMPT}`` variable (empty string means "nothing to add").
- ``tools``   : mapping ``tool_name -> state`` describing how each tool is
  affected relative to the current profile.
- ``skills``  : mapping ``skill_name -> state`` describing how each skill is
  affected relative to the current profile.

Each tool/skill ``state`` is one of three values:

- ``"enable"``    : force the tool/skill on, regardless of profile settings.
- ``"remove"``    : force the tool/skill off, regardless of profile settings.
- ``"no_change"`` : leave the tool/skill as configured in the current profile.

Only the infrastructure is provided here; the UI is added separately.
"""

import json

# Valid three-state values for tools and skills inside a mode.
ENABLE = "enable"
REMOVE = "remove"
NO_CHANGE = "no_change"
VALID_STATES = (ENABLE, REMOVE, NO_CHANGE)

# Built-in modes that every installation ships with. They are merged into the
# stored ``modes`` setting on load if missing; "Normal" can never be deleted.
PLAN_MODE_PROMPT = """## Plan Mode

You are operating in **Plan Mode**. In this mode you must NOT make any changes
to the system: do not execute commands, do not create, edit, or delete files,
and do not invoke any tool that has a side effect on the user's machine.

Your goal is to:
1. Explore and understand the request and the relevant code/context.
2. Design a concrete, step-by-step implementation plan.
3. Present the plan to the user for approval before any action is taken.

If a piece of information is missing, ask the user. Reason about trade-offs
explicitly. Once the plan is approved, the user will switch out of Plan Mode
and you will be allowed to execute it."""

DEFAULT_MODES = {
    "Normal": {
        "prompt": "",
        "tools": {},
        "skills": {},
    },
    "Plan": {
        "prompt": PLAN_MODE_PROMPT,
        # The command execution tool is removed so the assistant cannot mutate
        # the user's machine while planning.
        "tools": {
            "execute_command": REMOVE,
        },
        "skills": {},
    },
}

# Name of the built-in mode that is always present and cannot be removed.
DEFAULT_MODE_NAME = "Normal"


class ModeManager:
    """Load, persist, and resolve Modes backed by a ``Gio.Settings`` object.

    The stored shape is::

        {
            "<mode_name>": {
                "prompt": "<str>",
                "tools":   {"<tool_name>": "<state>", ...},
                "skills":  {"<skill_name>": "<state>", ...},
            },
            ...
        }
    """

    def __init__(self, settings):
        self.settings = settings
        self._load_modes()
        self._load_active_mode()

    # ------------------------------------------------------------------ #
    # Loading / persistence
    # ------------------------------------------------------------------ #
    def _load_modes(self):
        """Load modes from settings, ensuring built-ins are always present."""
        try:
            modes = json.loads(self.settings.get_string("modes"))
        except (json.JSONDecodeError, TypeError):
            modes = {}

        # Merge built-in defaults on top of whatever is stored: a built-in
        # missing from storage is added; a user-edited built-in is preserved.
        merged = dict(DEFAULT_MODES)
        merged.update(modes)
        self.modes = merged

        # Persist the merged view so the schema always reflects reality.
        self._save_modes()

    def _save_modes(self):
        self.settings.set_string("modes", json.dumps(self.modes))

    def _load_active_mode(self):
        active = self.settings.get_string("current-mode")
        if active not in self.modes:
            active = DEFAULT_MODE_NAME
            self.settings.set_string("current-mode", active)
        self.active_mode = active

    # ------------------------------------------------------------------ #
    # Read accessors
    # ------------------------------------------------------------------ #
    def get_modes(self) -> dict:
        """Return the full ``{name: mode_dict}`` mapping (a copy)."""
        return {name: self._normalize_mode(data) for name, data in self.modes.items()}

    def get_mode(self, name: str) -> dict | None:
        """Return a normalized copy of a single mode, or ``None`` if unknown."""
        data = self.modes.get(name)
        if data is None:
            return None
        return self._normalize_mode(data)

    def get_active_mode_name(self) -> str:
        return self.active_mode

    def get_active_mode(self) -> dict:
        """Return the active mode (falls back to Normal if missing)."""
        data = self.modes.get(self.active_mode) or self.modes[DEFAULT_MODE_NAME]
        return self._normalize_mode(data)

    def get_active_mode_prompt(self) -> str:
        """Return the active mode's prompt text (empty string if none/empty)."""
        return self.get_active_mode().get("prompt", "") or ""

    def get_tool_override(self, tool_name: str) -> str:
        """Return the active mode's state for a tool (defaults to NO_CHANGE)."""
        tools = self.get_active_mode().get("tools", {})
        return tools.get(tool_name, NO_CHANGE)

    def get_skill_override(self, skill_name: str) -> str:
        """Return the active mode's state for a skill (defaults to NO_CHANGE)."""
        skills = self.get_active_mode().get("skills", {})
        return skills.get(skill_name, NO_CHANGE)

    # ------------------------------------------------------------------ #
    # Resolution helpers (apply the 3-state to a base boolean)
    # ------------------------------------------------------------------ #
    def resolve_tool_enabled(self, tool_name: str, base_enabled: bool) -> bool:
        """Apply the active mode's tool state to a profile-derived boolean."""
        override = self.get_tool_override(tool_name)
        if override == ENABLE:
            return True
        if override == REMOVE:
            return False
        return base_enabled

    def resolve_skill_enabled(self, skill_name: str, base_enabled: bool) -> bool:
        """Apply the active mode's skill state to a profile-derived boolean."""
        override = self.get_skill_override(skill_name)
        if override == ENABLE:
            return True
        if override == REMOVE:
            return False
        return base_enabled

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #
    def set_active_mode(self, name: str):
        """Switch the active mode. Raises ``ValueError`` if unknown."""
        if name not in self.modes:
            raise ValueError(f"Mode '{name}' not found")
        self.active_mode = name
        self.settings.set_string("current-mode", name)

    def create_mode(self, name: str, prompt: str = "", tools: dict | None = None, skills: dict | None = None):
        """Create a new mode. Overwrites an existing mode with the same name."""
        self.modes[name] = self._build_mode(prompt, tools, skills)
        self._save_modes()

    def update_mode(self, name: str, prompt: str | None = None, tools: dict | None = None, skills: dict | None = None):
        """Update fields of an existing mode. Raises ``ValueError`` if unknown.

        ``None`` arguments leave the corresponding field untouched.
        """
        if name not in self.modes:
            raise ValueError(f"Mode '{name}' not found")
        mode = self._normalize_mode(self.modes[name])
        if prompt is not None:
            mode["prompt"] = prompt
        if tools is not None:
            mode["tools"] = self._clean_state_map(tools)
        if skills is not None:
            mode["skills"] = self._clean_state_map(skills)
        self.modes[name] = mode
        self._save_modes()

    def delete_mode(self, name: str) -> bool:
        """Delete a mode. The built-in Normal mode cannot be deleted.

        Returns ``True`` if deleted, ``False`` if it was protected or unknown.
        """
        if name == DEFAULT_MODE_NAME:
            return False
        if name not in self.modes:
            return False
        del self.modes[name]
        self._save_modes()
        # If the active mode was removed, fall back to Normal.
        if self.active_mode == name:
            self.set_active_mode(DEFAULT_MODE_NAME)
        return True

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_mode(prompt, tools, skills) -> dict:
        return {
            "prompt": prompt or "",
            "tools": ModeManager._clean_state_map(tools or {}),
            "skills": ModeManager._clean_state_map(skills or {}),
        }

    @staticmethod
    def _normalize_mode(data: dict) -> dict:
        """Return a complete, validated mode dict from possibly partial data."""
        return {
            "prompt": data.get("prompt", "") or "",
            "tools": ModeManager._clean_state_map(data.get("tools", {})),
            "skills": ModeManager._clean_state_map(data.get("skills", {})),
        }

    @staticmethod
    def _clean_state_map(state_map) -> dict:
        """Keep only entries whose value is a valid state."""
        if not isinstance(state_map, dict):
            return {}
        return {name: state for name, state in state_map.items() if state in VALID_STATES}
