#!/usr/bin/env python3
"""
Newelle Command Line Interface

This module provides command-line access to Newelle's functionality, including:
- Sending messages to LLM
- Listing and managing profiles
- Listing TTS and STT options
- Modifying settings
"""

import sys
import os
import json
import argparse
import gettext
from typing import Optional
import threading
import time

# Add src to path if running directly
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from gi.repository import Gio, GLib
except ImportError:
    print("Error: GLib/Gio not found. This CLI requires GLib bindings.")
    sys.exit(1)

from .controller import NewelleController, NewelleSettings
from .constants import (
    AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, SETTINGS_GROUPS,
    AVAILABLE_PROMPTS, SCHEMA_ID
)
from .utility.replacehelper import ReplaceHelper

# Setup translations
_ = gettext.gettext


class NewelleCLI:
    """Command line interface for Newelle"""

    def __init__(self):
        self.controller: Optional[NewelleController] = None
        self.settings: Optional[Gio.Settings] = None
        self._initialized = False

    def _init_controller(self, headless=True):
        """Initialize the controller in headless or GUI mode"""
        if self._initialized:
            return

        # Initialize paths and controller
        self.controller = NewelleController(sys.path)
        self.controller.init_paths()
        self.controller.check_path_integrity()
        self.controller.load_integrations()
        self.controller.load_extensions()

        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.controller.newelle_settings = NewelleSettings()
        self.controller.newelle_settings.load_settings(self.settings)

        # Load chats
        self.controller.load_chats(self.controller.newelle_settings.chat_id)

        # Initialize handlers (without UI if headless)
        from .controller import HandlersManager
        self.controller.handlers = HandlersManager(
            self.settings,
            self.controller.extensionloader,
            self.controller.models_dir,
            self.controller.integrationsloader,
            self.controller.installing_handlers
        )
        self.controller.handlers.select_handlers(self.controller.newelle_settings)

        # Load handlers cache
        self.controller.handlers.cache_handlers()

        self._initialized = True

    def send_message(
        self,
        message: str,
        chat_id: Optional[int] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        profile: Optional[str] = None,
        stream: bool = False,
        with_tools: bool = True
    ) -> str:
        """
        Send a message to the LLM and get a response

        Args:
            message: The message to send
            chat_id: Chat ID to send message to (default: current selected)
            model: Model to use (overrides current setting)
            provider: Provider to use (overrides current setting)
            profile: Profile to use (overrides current setting)
            stream: Whether to stream the response
            with_tools: Whether to enable tools (requires UI widgets if False)

        Returns:
            str: The LLM response
        """
        self._init_controller(headless=not with_tools)

        # Select chat
        if chat_id is not None:
            if 0 <= chat_id < len(self.controller.chats):
                self.controller.chat_id = chat_id
                self.controller.chat = self.controller.chats[chat_id]["chat"]
            else:
                print(f"Error: Chat ID {chat_id} out of range (0-{len(self.controller.chats)-1})")
                sys.exit(1)

        # Apply profile if specified
        if profile:
            profiles = json.loads(self.settings.get_string("profiles"))
            if profile in profiles:
                old_profile = self.settings.get_string("current-profile")
                self.settings.set_string("current-profile", profile)
                self.controller.newelle_settings.load_settings(self.settings)
                # Reload handlers with new profile settings
                self.controller.handlers.select_handlers(self.controller.newelle_settings)
            else:
                print(f"Error: Profile '{profile}' not found")
                print(f"Available profiles: {list(profiles.keys())}")
                sys.exit(1)

        # Override model/provider if specified
        original_model = None
        if model:
            if model in AVAILABLE_LLMS:
                original_model = self.controller.newelle_settings.language_model
                self.settings.set_string("language-model", model)
                self.controller.newelle_settings.language_model = model
                # Reload LLM handler
                self.controller.handlers.llm.destroy()
                self.controller.handlers.select_handlers(self.controller.newelle_settings)
                # Wait for model to load
                self.controller.handlers.llm.load_model(None)
            else:
                print(f"Error: Model '{model}' not found")
                print(f"Available models: {list(AVAILABLE_LLMS.keys())}")
                sys.exit(1)

        # Handle provider (same as model in Newelle)
        if provider:
            if provider in AVAILABLE_LLMS:
                original_model = self.controller.newelle_settings.language_model
                self.settings.set_string("language-model", provider)
                self.controller.newelle_settings.language_model = provider
                # Reload LLM handler
                self.controller.handlers.llm.destroy()
                self.controller.handlers.select_handlers(self.controller.newelle_settings)
                # Wait for model to load
                self.controller.handlers.llm.load_model(None)
            else:
                print(f"Error: Provider '{provider}' not found")
                print(f"Available providers: {list(AVAILABLE_LLMS.keys())}")
                sys.exit(1)

        # Get history and prompts
        history = []
        for msg in self.controller.chat:
            history.append({
                "User": msg["User"],
                "Message": msg["Message"]
            })

        # Build system prompts
        prompts = []
        formatter = ReplaceHelper(ReplaceHelper.get_default_vars(), None)
        for prompt in self.controller.newelle_settings.bot_prompts:
            prompts.append(formatter.format(prompt))

        # Disable tools if requested
        if not with_tools:
            # Filter out the tools prompt
            prompts = [p for p in prompts if "Tools" not in p and "tool" not in p.lower()]

        # Prepare the message
        user_message = {
            "User": "User",
            "Message": message
        }
        history.append(user_message)

        # Send message to LLM
        try:
            if stream:
                # Streaming response
                print("Response:", end="", flush=True)
                response = ""

                def on_update(text):
                    nonlocal response
                    response += text
                    print(text, end="", flush=True)

                result = self.controller.handlers.llm.generate_text_stream(
                    message,
                    history[:-1],  # Exclude current message from history
                    prompts,
                    on_update
                )
                print()  # New line after streaming
                return result
            else:
                # Non-streaming response
                result = self.controller.handlers.llm.generate_text(
                    message,
                    history[:-1],  # Exclude current message from history
                    prompts
                )
                return result

        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            # Restore original model if we changed it
            if original_model:
                self.settings.set_string("language-model", original_model)
                self.controller.handlers.llm.destroy()
                self.controller.handlers.select_handlers(self.controller.newelle_settings)

    def list_profiles(self) -> dict:
        """List all available profiles"""
        self._init_controller()
        profiles = json.loads(self.settings.get_string("profiles"))
        current_profile = self.settings.get_string("current-profile")

        result = {
            "current": current_profile,
            "profiles": []
        }

        for name, profile_data in profiles.items():
            result["profiles"].append({
                "name": name,
                "settings_groups": profile_data.get("settings_groups", []),
                "has_picture": profile_data.get("picture") is not None
            })

        return result

    def list_tts(self) -> dict:
        """List all available TTS engines"""
        result = []
        for key, data in AVAILABLE_TTS.items():
            result.append({
                "key": key,
                "title": data.get("title", ""),
                "description": data.get("description", "")
            })
        return result

    def list_stt(self) -> dict:
        """List all available STT engines"""
        result = []
        for key, data in AVAILABLE_STT.items():
            result.append({
                "key": key,
                "title": data.get("title", ""),
                "description": data.get("description", "")
            })
        return result

    def list_models(self) -> dict:
        """List all available LLM models/providers"""
        result = []
        for key, data in AVAILABLE_LLMS.items():
            result.append({
                "key": key,
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "secondary": data.get("secondary", False)
            })
        return result

    def list_settings(self, group: Optional[str] = None) -> dict:
        """
        List settings

        Args:
            group: Settings group to list (if None, list all)

        Returns:
            dict: Settings information
        """
        self._init_controller()
        result = {"groups": []}

        if group:
            # List specific group
            if group in SETTINGS_GROUPS:
                group_info = SETTINGS_GROUPS[group]
                result["groups"].append({
                    "name": group,
                    "title": group_info["title"],
                    "description": group_info["description"],
                    "settings": self._get_settings_values(group_info["settings"])
                })
            else:
                print(f"Error: Settings group '{group}' not found")
                print(f"Available groups: {list(SETTINGS_GROUPS.keys())}")
                sys.exit(1)
        else:
            # List all groups
            for name, group_info in SETTINGS_GROUPS.items():
                result["groups"].append({
                    "name": name,
                    "title": group_info["title"],
                    "description": group_info["description"],
                    "settings": self._get_settings_values(group_info["settings"])
                })

        return result

    def _get_settings_values(self, setting_keys: list) -> list:
        """Get values for a list of setting keys"""
        result = []
        for key in setting_keys:
            try:
                value = self.settings.get_value(key).unpack()
                result.append({
                    "key": key,
                    "value": value
                })
            except Exception as e:
                result.append({
                    "key": key,
                    "value": None,
                    "error": str(e)
                })
        return result

    def change_setting(self, key: str, value: str):
        """
        Change a setting value

        Args:
            key: Setting key
            value: New value (as string, will be converted to appropriate type)
        """
        self._init_controller()

        # Check if setting exists
        try:
            current_value = self.settings.get_value(key)
        except Exception:
            print(f"Error: Setting '{key}' not found")
            sys.exit(1)

        # Convert value to appropriate type
        variant = current_value.get_type_string()

        try:
            if variant == 's':  # string
                self.settings.set_string(key, value)
            elif variant == 'i':  # integer
                self.settings.set_int(key, int(value))
            elif variant == 'b':  # boolean
                if value.lower() in ('true', '1', 'yes', 'on'):
                    self.settings.set_boolean(key, True)
                elif value.lower() in ('false', '0', 'no', 'off'):
                    self.settings.set_boolean(key, False)
                else:
                    raise ValueError(f"Invalid boolean value: {value}")
            elif variant == 'd':  # double
                self.settings.set_double(key, float(value))
            else:
                print(f"Error: Unsupported setting type: {variant}")
                sys.exit(1)

            print(f"Setting '{key}' updated to: {value}")

            # Reload settings if necessary
            self.controller.update_settings()

        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Error setting value: {e}")
            sys.exit(1)

    def change_profile(self, profile: str):
        """
        Change the current profile

        Args:
            profile: Profile name to switch to
        """
        self._init_controller()
        profiles = json.loads(self.settings.get_string("profiles"))

        if profile not in profiles:
            print(f"Error: Profile '{profile}' not found")
            print(f"Available profiles: {list(profiles.keys())}")
            sys.exit(1)

        self.settings.set_string("current-profile", profile)
        self.controller.newelle_settings.load_settings(self.settings)

        # Reload handlers with new profile settings
        self.controller.handlers.select_handlers(self.controller.newelle_settings)

        print(f"Switched to profile: {profile}")

    def list_chats(self) -> dict:
        """List all chats"""
        self._init_controller()
        current_chat_id = self.controller.newelle_settings.chat_id

        result = {
            "current_chat_id": current_chat_id,
            "chats": []
        }

        for i, chat_data in enumerate(self.controller.chats):
            result["chats"].append({
                "id": i,
                "name": chat_data.get("name", f"Chat {i+1}"),
                "message_count": len(chat_data.get("chat", [])),
                "is_current": i == current_chat_id
            })

        return result


def print_json(data: dict):
    """Print data as formatted JSON"""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    """Main entry point for CLI"""
    parser = argparse.ArgumentParser(
        description="Newelle Command Line Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send a message
  newelle-cli --send "Hello, how are you?"

  # Send message with specific model
  newelle-cli --send "Write a poem" --model ollama

  # Send message to specific chat
  newelle-cli --send "Continue" --chat 0

  # Send message without tools
  newelle-cli --send "Simple question" --no-tools

  # List all profiles
  newelle-cli --list-profiles

  # List TTS engines
  newelle-cli --list-tts

  # List STT engines
  newelle-cli --list-stt

  # List models
  newelle-cli --list-models

  # List all settings
  newelle-cli --list-settings

  # List specific settings group
  newelle-cli --list-settings LLM

  # Change a setting
  newelle-cli --set-setting "language-model" "ollama"

  # Change profile
  newelle-cli --set-profile "MyProfile"

  # List chats
  newelle-cli --list-chats
        """
    )

    # Main action options (mutually exclusive)
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--send",
        metavar="MESSAGE",
        help="Send a message to the LLM and get a response"
    )
    action_group.add_argument(
        "--list-profiles",
        action="store_true",
        help="List all available profiles"
    )
    action_group.add_argument(
        "--list-tts",
        action="store_true",
        help="List all available TTS engines"
    )
    action_group.add_argument(
        "--list-stt",
        action="store_true",
        help="List all available STT engines"
    )
    action_group.add_argument(
        "--list-models",
        action="store_true",
        help="List all available LLM models/providers"
    )
    action_group.add_argument(
        "--list-settings",
        nargs="?",
        const=None,
        metavar="GROUP",
        help="List settings (all or specific group)"
    )
    action_group.add_argument(
        "--list-chats",
        action="store_true",
        help="List all chats"
    )

    # Additional options for --send
    parser.add_argument(
        "--chat",
        type=int,
        metavar="ID",
        help="Chat ID to send message to (default: current)"
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Model to use (overrides current setting)"
    )
    parser.add_argument(
        "--provider",
        metavar="PROVIDER",
        help="Provider to use (overrides current setting)"
    )
    parser.add_argument(
        "--profile",
        metavar="PROFILE",
        help="Profile to use (overrides current setting)"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream the response"
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Disable tools (requires UI widgets if False)"
    )

    # Settings modification options
    parser.add_argument(
        "--set-setting",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="Change a setting value"
    )
    parser.add_argument(
        "--set-profile",
        metavar="PROFILE",
        help="Change the current profile"
    )

    # Output format
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )

    args = parser.parse_args()

    # Initialize CLI
    cli = NewelleCLI()

    # Handle actions
    if args.send:
        # Send message
        response = cli.send_message(
            message=args.send,
            chat_id=args.chat,
            model=args.model,
            provider=args.provider,
            profile=args.profile,
            stream=args.stream,
            with_tools=not args.no_tools
        )
        if not args.stream:
            print(response)

    elif args.list_profiles:
        # List profiles
        data = cli.list_profiles()
        if args.output == "json":
            print_json(data)
        else:
            print(f"Current profile: {data['current']}\n")
            print("Available profiles:")
            for profile in data["profiles"]:
                current_marker = " (current)" if profile["name"] == data["current"] else ""
                print(f"  - {profile['name']}{current_marker}")
                print(f"    Settings groups: {', '.join(profile['settings_groups']) or 'None'}")
                if profile["has_picture"]:
                    print(f"    Has picture: Yes")
                print()

    elif args.list_tts:
        # List TTS engines
        data = cli.list_tts()
        if args.output == "json":
            print_json(data)
        else:
            print("Available TTS engines:")
            for tts in data:
                print(f"  - {tts['key']}: {tts['title']}")
                print(f"    {tts['description']}")
                print()

    elif args.list_stt:
        # List STT engines
        data = cli.list_stt()
        if args.output == "json":
            print_json(data)
        else:
            print("Available STT engines:")
            for stt in data:
                print(f"  - {stt['key']}: {stt['title']}")
                print(f"    {stt['description']}")
                print()

    elif args.list_models:
        # List models
        data = cli.list_models()
        if args.output == "json":
            print_json(data)
        else:
            print("Available LLM models/providers:")
            for model in data:
                secondary_marker = " (secondary)" if model["secondary"] else ""
                print(f"  - {model['key']}: {model['title']}{secondary_marker}")
                print(f"    {model['description']}")
                print()

    elif args.list_settings is not None:
        # List settings
        data = cli.list_settings(args.list_settings)
        if args.output == "json":
            print_json(data)
        else:
            for group in data["groups"]:
                print(f"{group['name']}: {group['title']}")
                print(f"  {group['description']}")
                print("  Settings:")
                for setting in group["settings"]:
                    if "error" in setting:
                        print(f"    - {setting['key']}: ERROR - {setting['error']}")
                    else:
                        print(f"    - {setting['key']}: {setting['value']}")
                print()

    elif args.list_chats:
        # List chats
        data = cli.list_chats()
        if args.output == "json":
            print_json(data)
        else:
            print(f"Current chat ID: {data['current_chat_id']}\n")
            print("Available chats:")
            for chat in data["chats"]:
                current_marker = " (current)" if chat["is_current"] else ""
                print(f"  - [{chat['id']}] {chat['name']}{current_marker}")
                print(f"    Messages: {chat['message_count']}")
                print()

    if args.set_setting:
        # Change setting
        cli.change_setting(args.set_setting[0], args.set_setting[1])

    if args.set_profile:
        # Change profile
        cli.change_profile(args.set_profile)


if __name__ == "__main__":
    main()
