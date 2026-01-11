import sys
import os
import gettext
import gi 
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
from .ui.settings import Settings
from .window import MainWindow
from .ui.shortcuts import Shortcuts
from .ui.thread_editing import ThreadEditing
from .ui.extension import Extension
from .ui.mini_window import MiniWindow
from .cli import NewelleCLI


class MyApp(Adw.Application):
    def __init__(self, version, **kwargs):
        self.version = version
        super().__init__(flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE, **kwargs)
        self.settings = Gio.Settings.new("io.github.qwersyk.Newelle")
        
        # Existing action option
        self.add_main_option("run-action", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Run an action", "ACTION")
        
        # CLI options for sending messages
        self.add_main_option("cli-send", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Send a message to LLM", "MESSAGE")
        self.add_main_option("cli-chat", 0, GLib.OptionFlags.NONE, GLib.OptionArg.INT, "Chat ID to send message to", "ID")
        self.add_main_option("cli-model", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Model to use (overrides setting)", "MODEL")
        self.add_main_option("cli-provider", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Provider to use (overrides setting)", "PROVIDER")
        self.add_main_option("cli-profile", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Profile to use (overrides setting)", "PROFILE")
        self.add_main_option("cli-stream", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Stream response", None)
        self.add_main_option("cli-no-tools", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Disable tools", None)
        
        # CLI options for listing
        self.add_main_option("cli-list-profiles", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "List all profiles", None)
        self.add_main_option("cli-list-tts", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "List TTS engines", None)
        self.add_main_option("cli-list-stt", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "List STT engines", None)
        self.add_main_option("cli-list-models", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "List LLM models/providers", None)
        self.add_main_option("cli-list-chats", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "List all chats", None)
        self.add_main_option("cli-list-settings", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "List settings (all or specific group)", "GROUP")
        
        # CLI options for settings
        self.add_main_option("cli-set-setting", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Change a setting", "KEY")
        self.add_main_option("cli-setting-value", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Setting value", "VALUE")
        self.add_main_option("cli-set-profile", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Change current profile", "PROFILE")
        
        # CLI output format
        self.add_main_option("cli-json", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Output as JSON", None)
        css = '''
        .code{
        background-color: rgb(38,38,38);
        }

        .code .sourceview text{
            background-color: rgb(38,38,38);
        }
        .code .sourceview border gutter{
            background-color: rgb(38,38,38);
        }
        .sourceview{
            color: rgb(192,191,188);
        }
        .copy-action{
            color:rgb(255,255,255);
            background-color: rgb(38,162,105);
        }
        .large{
            -gtk-icon-size:100px;
        }
        .empty-folder{
            font-size:25px;
            font-weight:800;
            -gtk-icon-size:120px;
        }
        .user{
            background-color: rgba(61, 152, 255,0.03);
        }
        .assistant{
            background-color: rgba(184, 134, 17,0.02);
        }
        .done{
            background-color: rgba(33, 155, 98,0.02);
        }
        .failed{
            background-color: rgba(254, 31, 41,0.02);
        }
        .file{
            background-color: rgba(222, 221, 218,0.03);
        }
        .folder{
            background-color: rgba(189, 233, 255,0.03);
        }
        .message-warning{
            background-color: rgba(184, 134, 17,0.02);
        }
        .transparent{
            background-color: rgba(0,0,0,0);
        }
        .chart{
            background-color: rgba(61, 152, 255,0.25);
        }
        .right-angles{
            border-radius: 0;
        }
        .image{
            -gtk-icon-size:400px;
        }
        .video {
            min-height: 400px;
        }
        .mini-window {
            border-radius: 12px;
            border: 1px solid alpha(@card_fg_color, 0.15);
            box-shadow: 0 2px 4px alpha(black, 0.1);
            margin: 4px;
        }
        @keyframes pulse_opacity {
          0% { opacity: 1.0; }
          50% { opacity: 0.5; }
          100% { opacity: 1.0; }
        }

        .pulsing-label {
          animation-name: pulse_opacity;
          animation-duration: 1.8s;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
        }
        '''
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css, -1)
        display = Gdk.Display.get_default() 
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.connect('activate', self.on_activate)
        action = Gio.SimpleAction.new("about", None)
        action.connect('activate', self.on_about_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("shortcuts", None)
        action.connect('activate', self.on_shortcuts_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("settings", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("thread_editing", None)
        action.connect('activate', self.thread_editing_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("extension", None)
        action.connect('activate', self.extension_action)
        self.add_action(action)
    
    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

    def on_shortcuts_action(self, *a):
        shortcuts = Shortcuts(self)
        shortcuts.present()

    def on_about_action(self, *a):
        Adw.AboutWindow(transient_for=self.props.active_window,
                        application_name='Newelle',
                        application_icon='io.github.qwersyk.Newelle',
                        developer_name='qwersyk',
                        version=self.version,
                        issue_url='https://github.com/qwersyk/Newelle/issues',
                        website='https://github.com/qwersyk/Newelle',
                        developers=['Yehor Hliebov  https://github.com/qwersyk',"Francesco Caracciolo https://github.com/FrancescoCaracciolo", "Pim Snel https://github.com/mipmip"],
                        documenters=["Francesco Caracciolo https://github.com/FrancescoCaracciolo"],
                        designers=["Nokse22 https://github.com/Nokse22", "Jared Tweed https://github.com/JaredTweed"],
                        translator_credits="\n".join(["Amine Saoud (Arabic) https://github.com/amiensa","Heimen Stoffels (Dutch) https://github.com/Vistaus","Albano Battistella (Italian) https://github.com/albanobattistella","Oliver Tzeng (Traditional Chinese, all languages) https://github.com/olivertzeng","Aritra Saha (Bengali, Hindi) https://github.com/olumolu"]),
                        copyright='© 2025 qwersyk').present()

    def thread_editing_action(self, *a):
        threadediting = ThreadEditing(self)
        threadediting.present()

    def settings_action(self, *a): 
        settings = Settings(self, self.win.controller)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings

    def settings_action_paged(self, page=None, *a): 
        settings = Settings(self, self.win.controller, False, page)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings
    
    def close_settings(self, *a):
        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        settings.set_int("chat", self.win.chat_id)
        settings.set_string("path", os.path.normpath(self.win.main_path))
        self.win.update_settings()
        self.settingswindow.destroy()
        return True

    def extension_action(self, *a):
        extension = Extension(self)
        def close(win):
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            settings.set_int("chat", self.win.chat_id)
            settings.set_string("path", os.path.normpath(self.win.main_path))
            self.win.update_settings()
            win.destroy()
            return True
        extension.connect("close-request", close) 
        extension.present()
    
    def stdout_monitor_action(self, *a):
        """Show the stdout monitor dialog"""
        self.win.show_stdout_monitor_dialog()
    
    def close_window(self, *a):
        if hasattr(self,"mini_win"):
            self.mini_win.close()
        if all(element.poll() is not None for element in self.win.streams):
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            settings.set_int("window-width", self.win.get_width())
            settings.set_int("window-height", self.win.get_height())
            self.win.controller.close_application()
            return False
        else:
            dialog = Adw.MessageDialog(
                transient_for=self.win,
                heading=_("Terminal threads are still running in the background"),
                body=_("When you close the window, they will be automatically terminated"),
                body_use_markup=True
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", self.close_message)
            dialog.present()
            return True
    
    def close_message(self,a,status):
        if status=="close":
            for i in self.win.streams:
                i.terminate()
            self.win.controller.close_application()
            self.win.destroy()
    
    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        
        # Check for CLI mode flags (don't require GUI)
        if self._check_and_run_cli_mode(options, command_line):
            return 0
            
        # Existing action handling
        if options.contains("run-action"):
            action_name = options.lookup_value("run-action").get_string()
            if self.lookup_action(action_name):
                self.activate_action(action_name, None)
            else:
                command_line.printerr(f"Action '{action_name}' not found.\n")
                return 1
        
        self.activate()
        return 0

    def _check_and_run_cli_mode(self, options, command_line):
        """Check if CLI mode is requested and run CLI commands"""
        # List CLI flags that indicate CLI mode
        cli_flags = [
            'cli-send', 'cli-list-profiles', 'cli-list-tts', 
            'cli-list-stt', 'cli-list-models', 'cli-list-settings',
            'cli-list-chats', 'cli-set-setting', 'cli-set-profile'
        ]
        
        for flag in cli_flags:
            if options.contains(flag):
                # CLI mode detected
                return self._run_cli_mode(options, command_line)
        
        return False

    def _run_cli_mode(self, options, command_line):
        """Run in CLI mode without GUI"""
        try:
            cli = NewelleCLI()
            
            # Parse CLI arguments from options
            args = {}
            
            if options.contains("cli-send"):
                message = options.lookup_value("cli-send").get_string()
                chat_id = options.lookup_value("cli-chat").get_int32() if options.contains("cli-chat") else None
                model = options.lookup_value("cli-model").get_string() if options.contains("cli-model") else None
                provider = options.lookup_value("cli-provider").get_string() if options.contains("cli-provider") else None
                profile = options.lookup_value("cli-profile").get_string() if options.contains("cli-profile") else None
                stream = options.contains("cli-stream")
                no_tools = options.contains("cli-no-tools")
                
                response = cli.send_message(
                    message=message,
                    chat_id=chat_id,
                    model=model,
                    provider=provider,
                    profile=profile,
                    stream=stream,
                    with_tools=not no_tools
                )
                if not stream:
                    print(response)
                return True
                
            elif options.contains("cli-list-profiles"):
                data = cli.list_profiles()
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-list-tts"):
                data = cli.list_tts()
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-list-stt"):
                data = cli.list_stt()
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-list-models"):
                data = cli.list_models()
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-list-settings"):
                group = options.lookup_value("cli-list-settings").get_string() if options.lookup_value("cli-list-settings") is not None else None
                if group == "":
                    group = None
                data = cli.list_settings(group)
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-list-chats"):
                data = cli.list_chats()
                self._print_cli_output(data, options)
                return True
                
            elif options.contains("cli-set-setting"):
                key = options.lookup_value("cli-set-setting").get_string()
                value = options.lookup_value("cli-setting-value").get_string()
                cli.change_setting(key, value)
                return True
                
            elif options.contains("cli-set-profile"):
                profile = options.lookup_value("cli-set-profile").get_string()
                cli.change_profile(profile)
                return True
                
        except Exception as e:
            command_line.printerr(f"CLI Error: {e}\n")
            import traceback
            traceback.print_exc()
            return 1
        
        return False

    def _print_cli_output(self, data, options):
        """Print CLI output in specified format"""
        use_json = options.contains("cli-json")
        
        if use_json:
            import json
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            # Format as text
            self._format_text_output(data)

    def _format_text_output(self, data):
        """Format data as readable text"""
        if isinstance(data, dict):
            if "profiles" in data:
                # List profiles
                print(f"Current profile: {data['current']}\n")
                print("Available profiles:")
                for profile in data["profiles"]:
                    current_marker = " (current)" if profile["name"] == data["current"] else ""
                    print(f"  - {profile['name']}{current_marker}")
                    print(f"    Settings groups: {', '.join(profile['settings_groups']) or 'None'}")
                    if profile["has_picture"]:
                        print(f"    Has picture: Yes")
                    print()
            elif "groups" in data:
                # List settings
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
            elif "chats" in data:
                # List chats
                print(f"Current chat ID: {data['current_chat_id']}\n")
                print("Available chats:")
                for chat in data["chats"]:
                    current_marker = " (current)" if chat["is_current"] else ""
                    print(f"  - [{chat['id']}] {chat['name']}{current_marker}")
                    print(f"    Messages: {chat['message_count']}")
                    print()
            else:
                # Generic dict
                for key, value in data.items():
                    print(f"{key}: {value}")
        elif isinstance(data, list):
            # List of items (TTS, STT, models)
            if data and "key" in data[0]:
                # These are handler items
                for item in data:
                    title = item.get("title", "")
                    description = item.get("description", "")
                    secondary = " (secondary)" if item.get("secondary", False) else ""
                    print(f"  - {item['key']}: {title}{secondary}")
                    print(f"    {description}")
                    print()
            else:
                for item in data:
                    print(f"  - {item}")
        else:
            print(data)

    def on_activate(self, app):
        if not hasattr(self,"win"):
            self.win = MainWindow(application=app)
            self.win.connect("close-request", self.close_window)

        if self.settings.get_string("startup-mode") == "mini":
            if hasattr(self,"mini_win"):
                self.mini_win.close()
            self.mini_win = MiniWindow(application=self, main_window=self.win)
            self.mini_win.present()
            self.settings.set_string("startup-mode", "normal")
        else:
            self.win.present()

    def focus_message(self, *a):
        self.win.focus_input()

    def reload_chat(self,*a):
        self.win.show_chat()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is rebooted')))

    def reload_folder(self,*a):
        self.win.update_folder()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Folder is rebooted')))

    def new_chat(self,*a):
        self.win.new_chat(None)
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is created')))

    def start_recording(self,*a):
        if not self.win.recording:
            self.win.start_recording(self.win.recording_button)
        else:
            self.win.stop_recording(self.win.recording_button)

    def stop_tts(self,*a):
        self.win.mute_tts(self.win.mute_tts_button)

    def stop_chat(self, *a):
        if hasattr(self, "win") and not self.win.status:
            self.win.stop_chat()
    
    def do_shutdown(self):
        self.win.save_chat()
        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        settings.set_int("chat", self.win.chat_id)
        settings.set_string("path", os.path.normpath(self.win.main_path))
        self.win.stream_number_variable += 1
        Gtk.Application.do_shutdown(self)

    def zoom(self, *a):
        zoom = min(250, self.settings.get_int("zoom") + 10)
        self.win.set_zoom(zoom)
        self.settings.set_int("zoom", zoom)

    def zoom_out(self, *a):
        zoom = max(100, self.settings.get_int("zoom") - 10)
        self.win.set_zoom(zoom)
        self.settings.set_int("zoom", zoom)
    
    def save(self, *a):
        self.win.save()
    def pretty_print_chat(self, *a):
        for msg in self.win.chat:
            print(msg["User"], msg["Message"])
    def debug(self, *a):
        self.pretty_print_chat()

def main(version):
    app = MyApp(application_id="io.github.qwersyk.Newelle", version = version)
    app.create_action('reload_chat', app.reload_chat, ['<primary>r'])
    app.create_action('reload_folder', app.reload_folder, ['<primary>e'])
    app.create_action('new_chat', app.new_chat, ['<primary>t'])
    app.create_action('focus_message', app.focus_message, ['<primary>l'])
    app.create_action('start_recording', app.start_recording, ['<primary>g'])
    app.create_action('stop_chat', app.stop_chat, ['<primary>q'])
    app.create_action('stop_tts', app.stop_tts, ['<primary>k'])
    app.create_action('save', app.save, ['<primary>s'])
    app.create_action('zoom', app.zoom, ['<primary>plus'])
    app.create_action('zoom', app.zoom, ['<primary>equal'])
    app.create_action('zoom_out', app.zoom_out, ['<primary>minus'])
    app.create_action('debug', app.debug, ['<primary>b'])
    app.run(sys.argv)
