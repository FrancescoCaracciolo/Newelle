from gi.repository import Gtk, Adw, GLib
from ..extra_settings import ExtraSettingsBuilder
from .image_generator import ImageGeneratorWidget
import uuid


class ImageGeneratorMiniApp(Gtk.Box):
    """A self-contained mini app widget for image generation.

    Provides a prompt entry, generate button, image display with save capability,
    and inline editing of the image generator handler's extra settings.
    Designed to be embedded as a canvas tab.
    """

    def __init__(self, handler, constants, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.handler = handler
        self.constants = constants
        self._generating = False

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
        )

        prompt_label = Gtk.Label(
            label=_("Image Prompt"),
            halign=Gtk.Align.START,
            css_classes=["heading"],
        )
        content.append(prompt_label)

        self.prompt_entry = Gtk.Entry(
            placeholder_text=_("Describe the image you want to generate..."),
            hexpand=True,
        )
        self.prompt_entry.connect("activate", lambda e: self.on_generate())
        content.append(self.prompt_entry)

        self.generate_button = Gtk.Button(
            label=_("Generate Image"),
            css_classes=["suggested-action", "pill"],
            halign=Gtk.Align.CENTER,
            margin_top=4,
        )
        self.generate_button.connect("clicked", lambda b: self.on_generate())
        content.append(self.generate_button)

        self.image_widget = ImageGeneratorWidget(width=380, height=380)
        self.image_widget.set_halign(Gtk.Align.CENTER)
        self.image_widget.show_loading(False)
        content.append(self.image_widget)

        content.append(Gtk.Separator(margin_top=6, margin_bottom=6))

        settings_group = Adw.PreferencesGroup(title=_("Settings"))
        content.append(settings_group)

        self.settingsrows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.settingsrows,
            convert_constants=self._convert_constants,
        )

        self._build_settings_rows(settings_group)

        self.handler.set_extra_settings_update(
            lambda _: GLib.idle_add(
                self.extra_settings_builder.on_setting_change,
                self.constants, self.handler, self.handler.key, True,
            )
        )

        scroll.set_child(content)
        self.append(scroll)

    def _convert_constants(self, constants):
        return "image_generator"

    def _build_settings_rows(self, group):
        row_key = (
            self.handler.key,
            self.extra_settings_builder.convert_constants(self.constants),
            self.handler.is_secondary(),
        )
        self.settingsrows[row_key] = {
            "row": group,
            "extra_settings": [],
            "extra_settings_loaded": True,
        }
        self.extra_settings_builder.add_extra_settings(
            self.constants, self.handler, group
        )

    def on_generate(self, button=None):
        """Handle the generate button click or Enter key."""
        if self._generating:
            return

        prompt = self.prompt_entry.get_text().strip()
        if not prompt:
            return

        self._generating = True
        self.generate_button.set_sensitive(False)
        self.image_widget.show_loading(True)
        self.image_widget.set_prompt(prompt)

        msg_uuid = str(uuid.uuid4())
        self.handler.generate_and_display(
            prompt, self.image_widget, msg_uuid, on_done_callback=self._on_generation_done
        )

    def _on_generation_done(self):
        """Re-enable the generate button."""
        self._generating = False
        self.generate_button.set_sensitive(True)
