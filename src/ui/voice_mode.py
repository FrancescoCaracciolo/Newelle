from __future__ import annotations

import math
import os
import struct
import tempfile
import threading
import time
import wave
from collections import deque
from enum import Enum

import gi
import pyaudio

from gi.repository import Gdk, GLib, Gtk

from ..utility.strings import clean_message_tts
from ..utility.vad import VoiceActivityDetector

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
except (ImportError, ValueError):
    Gtk4LayerShell = None


VOICE_CSS = """
.voice-mode-root {
    padding: 6px;
}
.voice-mode-window {
    background-color: transparent;
}
.voice-pill-shell {
    min-width: 240px;
    min-height: 56px;
    padding: 0 10px;
    border-radius: 999px;
    color: @window_fg_color;
    background-color: alpha(@window_bg_color, 0.96);
    border: 1px solid alpha(@window_fg_color, 0.13);
    box-shadow: 0 10px 30px alpha(black, 0.24);
}
.voice-wave-bar {
    min-width: 3px;
    border-radius: 999px;
    background-color: @accent_bg_color;
}
.voice-interaction-card {
    min-width: 390px;
    border-radius: 22px;
    padding: 14px;
    margin-bottom: 8px;
    color: @window_fg_color;
    background-color: alpha(@window_bg_color, 0.98);
    border: 1px solid alpha(@window_fg_color, 0.13);
    box-shadow: 0 12px 36px alpha(black, 0.28);
}
.voice-interaction-card.voice-interaction-below {
    margin-top: 8px;
    margin-bottom: 0;
}
.voice-interaction-title {
    font-weight: 700;
}
.voice-mode-error .voice-wave-bar {
    background-color: @error_bg_color;
}
.voice-mode-waiting .voice-wave-bar {
    background-color: @warning_bg_color;
}
"""


class VoiceSessionState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    RUNNING = "running"
    WAITING = "waiting"
    SPEAKING = "speaking"
    ERROR = "error"
    CLOSING = "closing"


class VoiceCaptureDecision(Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    NO_SPEECH = "no-speech"


class VoiceEndpointController:
    """Pure timing state used around the shared VAD implementation."""

    def __init__(self, started_at, start_timeout, endpoint_debounce):
        self.speech_started = False
        self.start_deadline = started_at + start_timeout
        self.endpoint_debounce = endpoint_debounce
        self.finalize_deadline = None

    def observe(self, is_speech, started, ended, now):
        if started:
            self.speech_started = True
            self.finalize_deadline = None
        if ended and self.speech_started:
            self.finalize_deadline = now + self.endpoint_debounce
        if (
            self.finalize_deadline is not None
            and not is_speech
            and now >= self.finalize_deadline
        ):
            return VoiceCaptureDecision.COMPLETE
        if not self.speech_started and now >= self.start_deadline:
            return VoiceCaptureDecision.NO_SPEECH
        return VoiceCaptureDecision.CONTINUE


class VoiceSessionController:
    """Own cancellation and interactive results independently of the window."""

    def __init__(self):
        self.state = VoiceSessionState.IDLE
        self.cancel_event = threading.Event()
        self.pending_results = set()

    def transition(self, state):
        if self.cancel_event.is_set() and state not in {
            VoiceSessionState.ERROR,
            VoiceSessionState.CLOSING,
        }:
            return False
        self.state = state
        return True

    def track_interaction(self, result):
        if self.cancel_event.is_set():
            result.cancel()
            return False
        self.pending_results.add(result)
        return True

    def resolve_interaction(self, result):
        self.pending_results.discard(result)

    def fail(self):
        self.state = VoiceSessionState.ERROR
        self.cancel_event.set()

    def complete(self):
        if self.cancel_event.is_set():
            return False
        self.state = VoiceSessionState.IDLE
        return True

    def cancel(self):
        self.state = VoiceSessionState.CLOSING
        self.cancel_event.set()
        for result in tuple(self.pending_results):
            result.cancel()


class MeanWaveform(Gtk.Box):
    """One five-bar wave showing the mean envelope for input and output."""

    def __init__(self, animations_enabled: bool = True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self._animations_enabled = animations_enabled
        self._bars = []
        for _ in range(5):
            bar = Gtk.Box(css_classes=["voice-wave-bar"], valign=Gtk.Align.CENTER)
            bar.set_size_request(3, 5)
            self._bars.append(bar)
            self.append(bar)
        self._smoothed_level = 0.0
        self._phase = 0.0
        self._animation_source = None
        self.set_idle()

    def set_input_level(self, level: float):
        self.stop_animation()
        level = max(0.0, min(1.0, float(level)))
        self._smoothed_level = self._smoothed_level * 0.68 + level * 0.32
        self._render(self._smoothed_level)
        return GLib.SOURCE_REMOVE

    def start_output_animation(self):
        if not self._animations_enabled:
            self._render(0.52)
            return
        if self._animation_source is None:
            self._phase = 0.0
            self._animation_source = GLib.timeout_add(55, self._animate_output)

    def _animate_output(self):
        self._phase += 0.34
        level = 0.43 + 0.28 * math.sin(self._phase) + 0.12 * math.sin(self._phase * 2.3)
        self._render(max(0.12, min(1.0, level)), animated=True)
        return GLib.SOURCE_CONTINUE

    def _render(self, level: float, animated: bool = False):
        shape = (0.45, 0.72, 1.0, 0.72, 0.45)
        for index, bar in enumerate(self._bars):
            variation = 1.0
            if animated:
                left = 0.78 + 0.22 * math.sin(self._phase + index * 0.8)
                right = 0.78 + 0.22 * math.sin(
                    self._phase + index * 0.8 + 0.45
                )
                variation = (left + right) / 2
            height = 4 + int(18 * level * shape[index] * variation)
            bar.set_size_request(3, max(4, height))

    def set_idle(self):
        self.stop_animation()
        self._smoothed_level = 0.0
        self._render(0.08)

    def stop_animation(self):
        if self._animation_source is not None:
            GLib.source_remove(self._animation_source)
            self._animation_source = None


class VoiceModeWindow(Gtk.Window):
    """Desktop-anchored, one-shot speech → tools → TTS surface."""

    SAMPLE_RATE = 16000
    CHUNK_SIZE = 512
    START_TIMEOUT_SECONDS = 15.0
    ENDPOINT_DEBOUNCE_SECONDS = 0.5

    def __init__(self, application, main_window, on_closed=None, **kwargs):
        super().__init__(application=application, **kwargs)
        self.main_window = main_window
        self.controller = main_window.controller
        self.settings = self.controller.settings
        self.on_closed = on_closed
        self.session = VoiceSessionController()
        self._cancel_event = self.session.cancel_event
        self._recording_thread = None
        self._processing_thread = None
        self._pending_results = self.session.pending_results
        self._tts_tokens = []
        self._tts_handler = None
        self._owns_tts = False
        self._resume_wakeword = False
        self._destroying = False
        self._layer_shell_active = False
        self._css_provider = None
        gtk_settings = Gtk.Settings.get_default()
        self._animations_enabled = gtk_settings is None or bool(
            gtk_settings.get_property("gtk-enable-animations")
        )
        self._transition_ms = 200 if self._animations_enabled else 0
        self._settings_changed_handler = None
        self._interaction_hide_source = None

        self.set_title(_("Newelle Voice Mode"))
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_focusable(False)
        self.set_default_size(240, -1)
        self.add_css_class("voice-mode-window")
        self._build_ui()
        self._apply_css()
        self._configure_position()
        self._settings_changed_handler = self.settings.connect(
            "changed", self._on_setting_changed
        )
        self.connect("close-request", self._on_close_request)

    @property
    def state(self):
        return self.session.state

    @state.setter
    def state(self, value):
        self.session.state = value

    def _build_ui(self):
        self.root_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            css_classes=["voice-mode-root"],
        )

        self.interaction_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=self._transition_ms,
            reveal_child=False,
        )
        # A vertically sliding revealer still contributes its child's width
        # while collapsed. Hide it entirely until interaction is required so
        # the 390px card cannot stretch the normal 240px pill.
        self.interaction_revealer.set_visible(False)
        self.interaction_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            css_classes=["voice-interaction-card"],
        )
        self.interaction_title = Gtk.Label(
            label=_("Action required"),
            xalign=0,
            css_classes=["voice-interaction-title"],
        )
        self.interaction_card.append(self.interaction_title)
        self.interaction_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            max_content_height=360,
            propagate_natural_height=True,
        )
        self.interaction_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self.interaction_scroll.set_child(self.interaction_box)
        self.interaction_card.append(self.interaction_scroll)
        self.interaction_revealer.set_child(self.interaction_card)
        self.root_box.append(self.interaction_revealer)

        self.pill = Gtk.CenterBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            css_classes=["voice-pill-shell"],
        )
        self.waveform = MeanWaveform(self._animations_enabled)
        self.pill.set_center_widget(self.waveform)
        self._set_status(_("Ready"))
        self.root_box.append(self.pill)
        self._sync_interaction_layout()
        self.set_child(self.root_box)

    def _sync_interaction_layout(self):
        position = self.settings.get_string("voice-mode-position")
        interaction_below = position.startswith("top-")
        self.interaction_card.remove_css_class("voice-interaction-below")
        if interaction_below:
            self.interaction_card.add_css_class("voice-interaction-below")
            self.interaction_revealer.set_transition_type(
                Gtk.RevealerTransitionType.SLIDE_DOWN
            )
            self.root_box.reorder_child_after(self.pill, None)
        else:
            self.interaction_revealer.set_transition_type(
                Gtk.RevealerTransitionType.SLIDE_UP
            )
            self.root_box.reorder_child_after(self.interaction_revealer, None)

    def _on_setting_changed(self, _settings, key):
        if key in {"voice-mode-position", "voice-mode-margin"}:
            self._sync_interaction_layout()
            self._apply_layer_anchors()
        elif key in {
            "voice-pill-theme",
            "voice-pill-background",
            "voice-pill-foreground",
            "voice-pill-accent",
            "voice-pill-opacity",
        }:
            self._apply_css()

    def _apply_css(self):
        self._remove_css_provider()
        theme = self.settings.get_string("voice-pill-theme")
        opacity = max(0.55, min(1.0, self.settings.get_double("voice-pill-opacity")))
        if theme == "light":
            background, foreground, accent = "#ffffff", "#202124", "#3584e4"
        elif theme == "dark":
            background, foreground, accent = "#202124", "#f7f7f8", "#78aeed"
        elif theme == "custom":
            background = self._valid_color(
                self.settings.get_string("voice-pill-background"), "#202124"
            )
            foreground = self._valid_color(
                self.settings.get_string("voice-pill-foreground"), "#f7f7f8"
            )
            accent = self._valid_color(
                self.settings.get_string("voice-pill-accent"), "#78aeed"
            )
        else:
            background = foreground = accent = None

        css = VOICE_CSS
        if background is not None:
            css += f"""
            .voice-pill-shell, .voice-interaction-card {{
                color: {foreground};
                background-color: alpha({background}, {opacity});
                border-color: alpha({foreground}, 0.14);
            }}
            .voice-wave-bar {{ background-color: {accent}; }}
            """
        elif opacity != 0.96:
            css += f"""
            .voice-pill-shell, .voice-interaction-card {{
                background-color: alpha(@window_bg_color, {opacity});
            }}
            """

        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(css.encode())
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )

    def _remove_css_provider(self):
        display = Gdk.Display.get_default()
        if display is not None and self._css_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._css_provider)
        self._css_provider = None

    @staticmethod
    def _valid_color(value: str, fallback: str) -> str:
        rgba = Gdk.RGBA()
        return value if value and rgba.parse(value) else fallback

    def _configure_position(self):
        if Gtk4LayerShell is None or not Gtk4LayerShell.is_supported():
            return
        try:
            Gtk4LayerShell.init_for_window(self)
            Gtk4LayerShell.set_namespace(self, "newelle-voice-mode")
            Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
            Gtk4LayerShell.set_exclusive_zone(self, 0)
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.NONE
            )
            self._layer_shell_active = True
            self._apply_layer_anchors()
        except Exception as exc:
            self._layer_shell_active = False
            print(f"Voice Mode: Layer Shell unavailable: {exc}")

    def _apply_layer_anchors(self):
        if not self._layer_shell_active:
            return
        position = self.settings.get_string("voice-mode-position") or "bottom-center"
        valid_positions = {
            f"{vertical}-{horizontal}"
            for vertical in ("top", "center", "bottom")
            for horizontal in ("left", "center", "right")
        }
        if position not in valid_positions:
            position = "bottom-center"
        vertical, horizontal = position.split("-", 1)
        edges = {
            "top": Gtk4LayerShell.Edge.TOP,
            "bottom": Gtk4LayerShell.Edge.BOTTOM,
            "left": Gtk4LayerShell.Edge.LEFT,
            "right": Gtk4LayerShell.Edge.RIGHT,
        }
        for edge in edges.values():
            Gtk4LayerShell.set_anchor(self, edge, False)
            Gtk4LayerShell.set_margin(self, edge, 0)
        margin = max(0, self.settings.get_int("voice-mode-margin"))
        if vertical in edges:
            Gtk4LayerShell.set_anchor(self, edges[vertical], True)
            Gtk4LayerShell.set_margin(self, edges[vertical], margin)
        if horizontal in edges:
            Gtk4LayerShell.set_anchor(self, edges[horizontal], True)
            Gtk4LayerShell.set_margin(self, edges[horizontal], margin)

    def start(self):
        if self.state is not VoiceSessionState.IDLE:
            return
        if self._microphone_busy():
            self._show_error(_("Microphone in use"))
            return

        tts = getattr(self.controller.handlers, "tts", None)
        if tts is not None:
            tts.stop()

        self._resume_wakeword = bool(self.main_window.wakeword_listening)
        if self._resume_wakeword:
            self.main_window.stop_wakeword_detection()
            self._wakeword_release_deadline = time.monotonic() + 3.0
            GLib.timeout_add(25, self._start_after_wakeword_release)
        else:
            self._start_capture()

    def _start_after_wakeword_release(self):
        if self._cancel_event.is_set():
            return GLib.SOURCE_REMOVE
        detector = getattr(self.main_window, "wakeword_detector", None)
        if detector is not None and not detector.is_stopped():
            if time.monotonic() < self._wakeword_release_deadline:
                return GLib.SOURCE_CONTINUE
            self._show_error(_("Microphone is still busy"))
            return GLib.SOURCE_REMOVE
        self._start_capture()
        return GLib.SOURCE_REMOVE

    def _microphone_busy(self) -> bool:
        if self.main_window.recording or self.main_window._recording_stopping:
            return True
        tab_view = getattr(self.main_window, "canvas_tabs", None)
        if tab_view is None:
            return False
        for index in range(tab_view.get_n_pages()):
            child = tab_view.get_nth_page(index).get_child()
            if getattr(child, "call_active", False):
                return True
        return False

    def _start_capture(self):
        if self._cancel_event.is_set():
            return
        self._set_state(VoiceSessionState.LISTENING)
        self._recording_thread = threading.Thread(
            target=self._capture_one_utterance,
            name="newelle-voice-capture",
            daemon=True,
        )
        self._recording_thread.start()

    def _capture_one_utterance(self):
        audio = None
        stream = None
        vad = VoiceActivityDetector(self.SAMPLE_RATE)
        prebuffer = deque(maxlen=int(self.SAMPLE_RATE / self.CHUNK_SIZE) + 1)
        frames = []
        endpoint = VoiceEndpointController(
            time.monotonic(),
            self.START_TIMEOUT_SECONDS,
            self.ENDPOINT_DEBOUNCE_SECONDS,
        )
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.SAMPLE_RATE,
                input=True,
                frames_per_buffer=self.CHUNK_SIZE,
            )
            while not self._cancel_event.is_set():
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                self._queue_input_level(data)
                prebuffer.append(data)
                is_speech, started, ended = vad.process_chunk(data)
                had_speech = endpoint.speech_started
                decision = endpoint.observe(
                    is_speech, started, ended, time.monotonic()
                )

                if endpoint.speech_started and not had_speech:
                    frames = list(prebuffer)
                elif endpoint.speech_started:
                    frames.append(data)

                if decision is VoiceCaptureDecision.COMPLETE:
                    break
                if decision is VoiceCaptureDecision.NO_SPEECH:
                    GLib.idle_add(self._show_error, _("No speech detected"))
                    return
        except Exception as exc:
            print(f"Voice Mode capture error: {exc}")
            GLib.idle_add(self._show_error, _("Microphone unavailable"))
            return
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                try:
                    audio.terminate()
                except Exception:
                    pass
            self._recording_thread = None

        if self._cancel_event.is_set():
            return
        if not frames:
            GLib.idle_add(self._show_error, _("No speech detected"))
            return
        self._processing_thread = threading.Thread(
            target=self._process_capture,
            args=(b"".join(frames),),
            name="newelle-voice-request",
            daemon=True,
        )
        self._processing_thread.start()

    def _queue_input_level(self, data: bytes):
        try:
            count = len(data) // 2
            if count == 0:
                return
            samples = struct.unpack("<" + str(count) + "h", data)
            rms = math.sqrt(sum(sample * sample for sample in samples) / count)
            GLib.idle_add(self.waveform.set_input_level, min(1.0, rms / 9000.0))
        except Exception:
            pass

    def _process_capture(self, audio_data: bytes):
        audio_path = None
        try:
            GLib.idle_add(self._set_state, VoiceSessionState.TRANSCRIBING)
            stt = getattr(self.controller.handlers, "stt", None)
            if stt is None or not stt.is_installed():
                GLib.idle_add(self._show_error, _("Speech recognition unavailable"))
                return

            with tempfile.NamedTemporaryFile(
                dir=self.controller.cache_dir,
                prefix="voice_mode_",
                suffix=".wav",
                delete=False,
            ) as temporary_file:
                audio_path = temporary_file.name
            with wave.open(audio_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.SAMPLE_RATE)
                wav_file.writeframes(audio_data)

            text = stt.recognize_file(audio_path)
            if self._cancel_event.is_set():
                return
            if not text or not text.strip():
                GLib.idle_add(self._show_error, _("I didn't catch that"))
                return

            llm = getattr(self.controller.handlers, "llm", None)
            if llm is None or not llm.is_installed():
                GLib.idle_add(self._show_error, _("Language model unavailable"))
                return

            GLib.idle_add(self._set_state, VoiceSessionState.RUNNING)
            chat_id = self.controller.create_voice_chat()
            configured_mode = self.settings.get_string("voice-mode-mode")
            mode_name = None if configured_mode in ("", "current") else configured_mode
            if (
                mode_name is not None
                and self.controller.mode_manager.get_mode(mode_name) is None
            ):
                mode_name = None

            def on_tool_result(tool_name, result):
                GLib.idle_add(self._on_tool_result, tool_name, result)

            previous_call_request = self.controller.is_call_request
            self.controller.is_call_request = True
            try:
                response = self.controller.run_llm_with_tools(
                    message=text.strip(),
                    chat_id=chat_id,
                    on_tool_result_callback=on_tool_result,
                    save_chat=True,
                    force_tools_on_main_thread=True,
                    mode_name=mode_name,
                )
            finally:
                self.controller.is_call_request = previous_call_request

            if self._cancel_event.is_set():
                return
            spoken_response = clean_message_tts(response or "")
            if not spoken_response:
                GLib.idle_add(self._complete_without_tts)
                return
            self._play_response(spoken_response)
        except Exception as exc:
            import traceback

            print(f"Voice Mode request error: {exc}")
            print(traceback.format_exc())
            GLib.idle_add(self._show_error, _("Voice command failed"))
        finally:
            self._processing_thread = None
            if audio_path is not None:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _play_response(self, response: str):
        tts = getattr(self.controller.handlers, "tts", None)
        if tts is None or not tts.is_installed():
            GLib.idle_add(self._show_error, _("Speech synthesis unavailable"))
            return
        self._tts_tokens = [
            tts.connect("start", self._on_tts_start),
            tts.connect("stop", self._on_tts_stop),
        ]
        self._tts_handler = tts
        self._owns_tts = True
        try:
            tts.play(response)
        except Exception as exc:
            print(f"Voice Mode TTS error: {exc}")
            GLib.idle_add(self._show_error, _("Speech playback failed"))
        finally:
            self._owns_tts = False

    def _on_tts_start(self):
        GLib.idle_add(self._set_state, VoiceSessionState.SPEAKING)

    def _on_tts_stop(self):
        # Give a synchronous playback exception a chance to publish its error
        # state before treating the stop signal as successful completion.
        GLib.timeout_add(50, self._finish_after_speech)

    def _finish_after_speech(self):
        if not self._cancel_event.is_set():
            self.session.complete()
            self._set_status(_("Ready"))
            self.waveform.set_idle()
        return GLib.SOURCE_REMOVE

    def _complete_without_tts(self):
        if self._cancel_event.is_set():
            return GLib.SOURCE_REMOVE
        self.session.complete()
        self._set_status(_("Done"))
        self.waveform.set_idle()
        return GLib.SOURCE_REMOVE

    def _on_tool_result(self, tool_name, result):
        if self._cancel_event.is_set():
            result.cancel()
            return GLib.SOURCE_REMOVE
        self._set_status(tool_name.replace("_", " ").title())
        if not result.requires_interaction:
            return GLib.SOURCE_REMOVE

        if not self.session.track_interaction(result):
            return GLib.SOURCE_REMOVE
        self._set_state(VoiceSessionState.WAITING)
        self.interaction_title.set_label(
            _("{tool} needs your input").format(
                tool=tool_name.replace("_", " ").title()
            )
        )
        self._clear_interaction_box()
        widget = result.widget
        if widget is not None:
            parent = widget.get_parent()
            if parent is not None and hasattr(parent, "remove"):
                parent.remove(widget)
            self.interaction_box.append(widget)
        elif result.interaction_options:
            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for option in result.interaction_options:
                button = Gtk.Button(label=option.title)
                button.connect("clicked", lambda _button, cb=option.callback: cb())
                button_box.append(button)
            self.interaction_box.append(button_box)
        if self._interaction_hide_source is not None:
            GLib.source_remove(self._interaction_hide_source)
            self._interaction_hide_source = None
        self.interaction_revealer.set_visible(True)
        self.interaction_revealer.set_reveal_child(True)
        self.set_focusable(True)
        if self._layer_shell_active:
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
            )
        self.present()
        threading.Thread(
            target=self._wait_for_interaction,
            args=(result,),
            daemon=True,
        ).start()
        return GLib.SOURCE_REMOVE

    def _wait_for_interaction(self, result):
        result.get_output()
        GLib.idle_add(self._finish_interaction, result)

    def _finish_interaction(self, result):
        self.session.resolve_interaction(result)
        if self._destroying:
            return GLib.SOURCE_REMOVE
        if not self._pending_results:
            self.interaction_revealer.set_reveal_child(False)
            if self._transition_ms:
                self._interaction_hide_source = GLib.timeout_add(
                    self._transition_ms, self._hide_interaction_card
                )
            else:
                self._hide_interaction_card()
            self.set_focusable(False)
            if self._layer_shell_active:
                Gtk4LayerShell.set_keyboard_mode(
                    self, Gtk4LayerShell.KeyboardMode.NONE
                )
            if not self._cancel_event.is_set():
                self._set_state(VoiceSessionState.RUNNING)
        return GLib.SOURCE_REMOVE

    def _hide_interaction_card(self):
        self._interaction_hide_source = None
        if not self._pending_results and not self.interaction_revealer.get_reveal_child():
            self.interaction_revealer.set_visible(False)
            self.set_default_size(240, -1)
        return GLib.SOURCE_REMOVE

    def _clear_interaction_box(self):
        child = self.interaction_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.interaction_box.remove(child)
            child = next_child

    def _set_state(self, state: VoiceSessionState):
        if self._cancel_event.is_set() and state is not VoiceSessionState.CLOSING:
            return GLib.SOURCE_REMOVE
        if not self.session.transition(state):
            return GLib.SOURCE_REMOVE
        self._set_status(self._state_label(state))
        self.root_box.remove_css_class("voice-mode-error")
        self.root_box.remove_css_class("voice-mode-waiting")
        if state is VoiceSessionState.SPEAKING:
            self.waveform.start_output_animation()
        elif state is VoiceSessionState.LISTENING:
            self.waveform.set_idle()
        else:
            self.waveform.set_idle()
        if state is VoiceSessionState.WAITING:
            self.root_box.add_css_class("voice-mode-waiting")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _state_label(state: VoiceSessionState):
        return {
            VoiceSessionState.IDLE: _("Ready"),
            VoiceSessionState.LISTENING: _("Listening"),
            VoiceSessionState.TRANSCRIBING: _("Transcribing"),
            VoiceSessionState.RUNNING: _("Working"),
            VoiceSessionState.WAITING: _("Action needed"),
            VoiceSessionState.SPEAKING: _("Speaking"),
            VoiceSessionState.ERROR: _("Voice error"),
            VoiceSessionState.CLOSING: _("Closing"),
        }[state]

    def _set_status(self, text):
        self.pill.set_tooltip_text(text)
        self.pill.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _show_error(self, message: str):
        if self._destroying:
            return GLib.SOURCE_REMOVE
        self.session.fail()
        self._set_status(message)
        self.root_box.add_css_class("voice-mode-error")
        self.waveform.set_idle()
        return GLib.SOURCE_REMOVE

    def cancel(self):
        if self._destroying:
            return
        self.session.cancel()
        tts = self._tts_handler or getattr(self.controller.handlers, "tts", None)
        if self._owns_tts and tts is not None:
            tts.stop()
        GLib.idle_add(self._finalize_close)

    def _on_close_request(self, *_args):
        self.cancel()
        return True

    def _disconnect_tts(self):
        tts = self._tts_handler
        if tts is not None:
            for token in self._tts_tokens:
                tts.disconnect(token)
        self._tts_tokens = []
        self._tts_handler = None

    def _finalize_close(self):
        if self._destroying:
            return GLib.SOURCE_REMOVE
        self._destroying = True
        self.session.cancel()
        self.waveform.stop_animation()
        self._disconnect_tts()
        if self._interaction_hide_source is not None:
            GLib.source_remove(self._interaction_hide_source)
            self._interaction_hide_source = None
        if self._settings_changed_handler is not None:
            self.settings.disconnect(self._settings_changed_handler)
            self._settings_changed_handler = None
        self._remove_css_provider()
        if self._resume_wakeword and self.controller.newelle_settings.wakeword_enabled:
            GLib.idle_add(self.main_window.start_wakeword_detection)
        callback = self.on_closed
        self.on_closed = None
        self.destroy()
        if callback is not None:
            callback(self)
        return GLib.SOURCE_REMOVE
