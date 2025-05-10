
from pydoc import describe
import shutil
import os
from ..constants import SETTINGS_GROUPS
from gi.repository import Gdk, Gtk, Adw, Gio, GLib

class ProfileDialog(Adw.PreferencesDialog):
    def __init__(self, parent, profile_settings):
        super().__init__()
        self.pic_path = None
        self.profile_settings = profile_settings
        self.parent = parent
        self.profile_name = "Assistant " + str(len(self.profile_settings) + 1)

        self.set_title("Create Profile")
        self.set_search_enabled(False)

        self.page = Adw.PreferencesPage()
        self.add(self.page)

        self.avatar_group = Adw.PreferencesGroup()
        self.page.add(self.avatar_group)
        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)
        self.settings_group = Adw.PreferencesGroup(title=_("Settings"))
        self.page.add(self.settings_group) 
        self.button_group = Adw.PreferencesGroup()
        self.page.add(self.button_group)

        # Avatar
        self.avatar = Adw.Avatar(
            text=self.profile_name,
            show_initials=True,
            size=70,
        )
        self.avatar.set_margin_bottom(24)

        # Make avatar clickable
        click_recognizer = Gtk.GestureClick()
        click_recognizer.connect("pressed", self.on_avatar_clicked)
        self.avatar.add_controller(click_recognizer)

        self.avatar_group.add(self.avatar)

        row = Adw.EntryRow(title="Profile Name", text=self.profile_name)
        row.connect("changed", self.on_profile_name_changed)
        self.entry = row
        self.group.add(row)

        self.settings_row = Adw.ExpanderRow(title=_("Copied Settings"), subtitle=_("Settings that will be copied to the new profile"))
        self.build_settings_group()
        self.settings_group.add(self.settings_row)

        # Create Button
        self.create_button = Gtk.Button(label="Create")
        self.create_button.add_css_class("suggested-action")
        self.create_button.connect("clicked", self.on_create_clicked)
        self.button_group.add(self.create_button)

        # File Filter for image selection
        self.image_filter = Gtk.FileFilter()
        self.image_filter.set_name("Images")
        self.image_filter.add_mime_type("image/*")
        
        g = Adw.PreferencesGroup()
        warning = Gtk.Label(label=_("The settings of the current profile will be copied into the new one"), wrap=True)
        g.add(warning)
        self.page.add(g)

    def build_settings_group(self):
        self.settings_switches = {}
        for setting, group in SETTINGS_GROUPS.items():
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
            toggle.set_active(True)
            row = Adw.ActionRow(title=group["title"], subtitle=group["description"], vexpand=False)
            row.add_suffix(toggle)
            self.settings_row.add_row(row)
            self.settings_switches[setting] = toggle

    def on_profile_name_changed(self, entry):
        """Updates the avatar text when the profile name changes."""
        if len(entry.get_text()) > 30:
            self.create_button.grab_focus()
            entry.set_text(entry.get_text()[:30])
            return
        profile_name = entry.get_text()
        self.profile_name = profile_name
        if profile_name:
            self.avatar.set_text(profile_name)
        else:
            self.avatar.set_text(
                "Assistant " + str(len(self.profile_settings) + 1)
            )

    def on_avatar_clicked(self, gesture, n_press, x, y):
        """Opens the file chooser when the avatar is clicked."""
        # File Chooser
        filters = Gio.ListStore.new(Gtk.FileFilter)

        image_filter = Gtk.FileFilter(name="Images", patterns=["*.png", "*.jpg", "*.jpeg", "*.webp"])

        filters.append(image_filter)

        dialog = Gtk.FileDialog(title=_("Set profile picture"),
                                modal=True,
                                default_filter=image_filter,
                                filters=filters)
        dialog.open(self.parent, None, self.on_file_chosen)

    def on_file_chosen(self, dialog, result):
        """Handles the selected file from the file chooser."""
        
        try:
            file = dialog.open_finish(result)
        except Exception as _:
            return
        if file is None:
            return
        file_path = file.get_path()
        self.pic_path = file_path
        texture = Gdk.Texture.new_from_file(file)
        self.avatar.set_custom_image(
           texture 
        )
        self.avatar.set_show_initials(False)
        # Gotta do this to make it smaller
        self.avatar.get_last_child().get_last_child().set_icon_size(Gtk.IconSize.NORMAL)

    def on_create_clicked(self, button):
        """Handles the create button click."""

        if self.pic_path is not None:
            path = os.path.join(self.parent.path, "profiles")
            if not os.path.exists(path):
                os.makedirs(path)
            shutil.copy(self.pic_path, os.path.join(path, self.profile_name + ".png"))
            self.pic_path = os.path.join(path, self.profile_name + ".png")
        if not self.profile_name:
            toast = Adw.Toast.new("Please enter a profile name.")
            self.parent.add_toast(toast)
            return
        
        # Get the custom image from the avatar (if any)
        copied_settings = [setting for setting in self.settings_switches if self.settings_switches[setting].get_active()] 
        print(copied_settings)
        self.parent.create_profile(self.profile_name, self.pic_path, {}, copied_settings)
        GLib.idle_add(self.parent.switch_profile, self.profile_name)
        self.close()
