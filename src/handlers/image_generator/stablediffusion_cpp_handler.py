from .image_generator import ImageGeneratorHandler
from ...handlers.extra_settings import ExtraSettings
from ...utility.system import can_escape_sandbox, is_flatpak, get_spawn_command, has_backend, detect_cuda_version
from ...handlers import ErrorSeverity
import subprocess
import os
import platform
import threading
import shutil
import zipfile
import tempfile
import time
import socket
from gi.repository import Gtk, Adw, GLib, Gdk
import requests


class StableDiffusionCPPHandler(ImageGeneratorHandler):
    """Local image generation using stable-diffusion.cpp.

    Supports downloading prebuilt binaries or building from source
    with hardware acceleration (CUDA, Vulkan, ROCm).
    """

    key = "stablediffusioncpp"
    schema_key = "image-generator-settings"

    RELEASE_API_URL = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases"
    REPO_URL = "https://github.com/leejet/stable-diffusion.cpp.git"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.sd_cpp_path = os.path.join(self.path, "stable-diffusion.cpp")
        self.sd_binary_path = os.path.join(self.sd_cpp_path, "build", "bin", "sd-cli")
        self.sd_server_binary_path = os.path.join(self.sd_cpp_path, "build", "bin", "sd-server")
        self.model_folder = os.path.join(self.path, "sd_models")
        self.lora_folder = os.path.join(self.path, "sd_lora")
        self._installing = False
        self._server_process = None
        self._server_lock = threading.Lock()

        for folder in (self.model_folder, self.lora_folder):
            if not os.path.exists(folder):
                try:
                    os.makedirs(folder)
                except Exception:
                    pass

    def get_extra_settings(self) -> list:
        settings = []

        # Model selection
        model_list = self._get_model_list()
        settings.append(
            ExtraSettings.ComboSetting(
                "model",
                "Model",
                "Stable Diffusion model to use",
                model_list,
                model_list[0][1] if len(model_list) > 0 else "",
                refresh=lambda button: self._get_model_list(True),
                folder=self.model_folder,
            )
        )

        settings.append(
            ExtraSettings.EntrySetting(
                "custom_models_dir",
                "Custom Models Directory",
                "Additional directory to scan for model files (.safetensors, .ckpt, .gguf). Leave empty to disable.",
                "",
                update_settings=True,
            )
        )

        # Installation
        if not self._is_binary_installed():
            settings.append(
                ExtraSettings.ButtonSetting(
                    "install",
                    "Install StableDiffusionCPP",
                    "Download prebuilt binaries or build from source with hardware acceleration",
                    self.show_install_dialog,
                    label="Install",
                )
            )
        else:
            settings.append(
                ExtraSettings.ToggleSetting(
                    "gpu_acceleration",
                    "Hardware Acceleration",
                    "Enable hardware acceleration (requires GPU backend)",
                    False,
                )
            )
            if is_flatpak():
                settings.append(
                    ExtraSettings.ToggleSetting(
                        "use_system_sd",
                        "Use System sd-cli",
                        "Use system-installed sd-cli instead of built-in (requires sd-cli on host and sandbox escape)",
                        False,
                    )
                )
            settings.append(
                ExtraSettings.ButtonSetting(
                    "reinstall",
                    "Reinstall",
                    "Rebuild or re-download stable-diffusion.cpp",
                    self.show_install_dialog,
                    label="Reinstall",
                )
            )

            # Server mode toggle (only if binary is installed)
            settings.append(
                ExtraSettings.ToggleSetting(
                    "use_server",
                    "Use sd-server",
                    "Use the HTTP server (sd-server) instead of the CLI (sd-cli) for image generation. The server is faster for multiple generations but uses more memory.",
                    False,
                )
            )
            settings.append(
                ExtraSettings.SpinSetting(
                    "server_port",
                    "Server Port",
                    "Port for the sd-server HTTP server",
                    17860, 1024, 65535, 1, 1, 0,
                )
            )

        # Generation settings
        settings.append(
            ExtraSettings.NestedSetting(
                "generation_settings",
                "Generation Settings",
                "Configure image generation parameters",
                [
                    ExtraSettings.SpinSetting(
                        "width", "Width", "Image width in pixels",
                        512, 64, 2048, 8, 64, 0,
                    ),
                    ExtraSettings.SpinSetting(
                        "height", "Height", "Image height in pixels",
                        512, 64, 2048, 8, 64, 0,
                    ),
                    ExtraSettings.SpinSetting(
                        "steps", "Steps", "Number of sampling steps",
                        20, 1, 150, 1, 10, 0,
                    ),
                    ExtraSettings.EntrySetting(
                        "cfg_scale", "CFG Scale", "Unconditional guidance scale",
                        "7.0",
                    ),
                    ExtraSettings.EntrySetting(
                        "seed", "Seed", "RNG seed (-1 for random)",
                        "-1",
                    ),
                    ExtraSettings.ComboSetting(
                        "sampling_method",
                        "Sampling Method",
                        "Sampler to use for generation",
                        ["euler", "euler_a", "heun", "dpm2", "dpm++2m", "dpm++2mv2", "lcm"],
                        "euler_a",
                    ),
                    ExtraSettings.MultilineEntrySetting(
                        "positive_prompt_template",
                        "Positive Prompt Template",
                        "Template for positive prompt. [input] will be replaced with the user prompt.",
                        "[input]",
                    ),
                    ExtraSettings.MultilineEntrySetting(
                        "negative_prompt_template",
                        "Negative Prompt Template",
                        "Template for negative prompt. [input] will be replaced with the positive prompt.",
                        "",
                    ),
                    ExtraSettings.SpinSetting(
                        "clip_skip",
                        "CLIP Skip",
                        "Ignore last layers of CLIP network; 1 ignores none, 2 ignores one layer. <= 0 uses model default (1 for SD1.x, 2 for SD2.x)",
                        -1, -1, 12, 1, 1, 0,
                    ),
                ],
            )
        )

        # LoRA settings
        settings.append(
            ExtraSettings.NestedSetting(
                "lora_settings",
                "LoRA Settings",
                "Configure LoRA adapters. Place LoRA files in the folder below and reference them in your prompt with <lora:filename:multiplier>.",
                [
                    ExtraSettings.ToggleSetting(
                        "enable_lora",
                        "Enable LoRA",
                        "Enable LoRA support by passing --lora-model-dir to sd-cli/sd-server",
                        False,
                        folder=self.lora_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "lora_folder_path",
                        "LoRA Folder",
                        "Directory containing LoRA weights (.safetensors, .ckpt)",
                        "",
                        update_settings=True,
                    ),
                ],
            )
        )

        return settings

    def _get_model_list(self, update=False):
        """Get available model files in the model folder."""
        model_list = tuple()
        seen = set()

        for root, _, files in os.walk(self.model_folder):
            for file in files:
                if file.endswith((".safetensors", ".ckpt", ".pth", ".pt", ".gguf")):
                    file_name = os.path.splitext(file)[0]
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    model_list += ((file_name, relative_path),)
                    seen.add(file_name)

        custom_dir = self.get_setting("custom_models_dir", False, "") or ""
        if custom_dir:
            custom_dir = os.path.expanduser(custom_dir)
            if os.path.isdir(custom_dir) and os.path.abspath(custom_dir) != os.path.abspath(self.model_folder):
                for root, _, files in os.walk(custom_dir):
                    for file in files:
                        if file.endswith((".safetensors", ".ckpt", ".pth", ".pt", ".gguf")):
                            file_name = os.path.splitext(file)[0]
                            abs_path = os.path.abspath(os.path.join(root, file))
                            display_name = file_name
                            if display_name in seen:
                                display_name = f"{file_name} (custom)"
                            model_list.append((display_name, abs_path))
                            seen.add(display_name)

        if update:
            self.settings_update()

        return model_list

    def _get_lora_dir(self) -> str:
        """Return the LoRA folder path from settings, or the default."""
        path = self.get_setting("lora_folder_path", True, "")
        if path:
            path = os.path.expanduser(path)
            return path
        return self.lora_folder

    def _is_lora_enabled(self) -> bool:
        """Check if LoRA support is enabled."""
        return self.get_setting("enable_lora", False, False)

    def _resolve_model_path(self, value: str) -> str:
        """Resolve a model setting value to an absolute path."""
        if not value:
            return ""
        if os.path.isabs(value):
            return value
        return os.path.join(self.model_folder, value)

    def _is_binary_installed(self) -> bool:
        """Check if the sd-cli binary is installed and executable."""
        if os.path.exists(self.sd_binary_path) and os.access(self.sd_binary_path, os.X_OK):
            return True
        if os.path.exists(self.sd_server_binary_path) and os.access(self.sd_server_binary_path, os.X_OK):
            return True
        # Also check system PATH when use_system_sd is enabled
        if is_flatpak() and self.get_setting("use_system_sd", False, False):
            return shutil.which("sd-cli") is not None
        return False

    def _get_binary_path(self) -> str:
        """Get the path to the sd-cli binary."""
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        if use_system:
            return "sd-cli"
        if self.get_setting("gpu_acceleration", False, False) and self._is_binary_installed():
            return self.sd_binary_path
        # Fall back to system binary
        return "sd-cli"

    # ── Server mode ─────────────────────────────────────────────────

    def _get_server_binary_path(self) -> str:
        """Get the path to the sd-server binary."""
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        if use_system:
            return "sd-server"
        if self.get_setting("gpu_acceleration", False, False) and os.path.exists(self.sd_server_binary_path):
            return self.sd_server_binary_path
        return "sd-server"

    def _get_server_port(self) -> int:
        """Get the configured server port."""
        return self.get_setting("server_port", True, 17860)

    def _is_server_running(self) -> bool:
        """Check if the sd-server is already running on the configured port."""
        port = self._get_server_port()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                return result == 0
        except Exception:
            return False

    def _start_server(self) -> None:
        """Start the sd-server process if not already running."""
        with self._server_lock:
            if self._server_process is not None and self._server_process.poll() is None:
                return
            if self._is_server_running():
                return

            binary = self._get_server_binary_path()

            # Validate that the binary exists
            if binary == self.sd_server_binary_path and not os.path.exists(binary):
                raise FileNotFoundError(
                    f"sd-server binary not found at {binary}. "
                    "Please reinstall stable-diffusion.cpp or disable server mode."
                )
            if binary == "sd-server" and not shutil.which("sd-server"):
                raise FileNotFoundError(
                    "sd-server not found on system PATH. "
                    "Please install it or disable server mode."
                )

            model = self._resolve_model_path(self.get_setting("model"))
            port = self._get_server_port()

            cmd = [
                binary,
                "-m", model,
                "--listen-port", str(port),
            ]

            if self._is_lora_enabled():
                cmd.extend(["--lora-model-dir", self._get_lora_dir()])

            clip_skip = self.get_setting("clip_skip", True, -1)
            if clip_skip > 0:
                cmd.extend(["--clip-skip", str(int(clip_skip))])

            use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
            use_spawn = is_flatpak() and (use_system or (os.path.exists(self.sd_server_binary_path) and self.get_setting("gpu_acceleration", False, False)))

            env = os.environ.copy()
            if binary == self.sd_server_binary_path:
                bin_dir = os.path.dirname(binary)
                if use_spawn:
                    cmd = get_spawn_command() + [f"--env=LD_LIBRARY_PATH={bin_dir}"] + cmd
                else:
                    existing = env.get("LD_LIBRARY_PATH", "")
                    env["LD_LIBRARY_PATH"] = bin_dir if not existing else f"{bin_dir}:{existing}"
            elif use_spawn:
                cmd = get_spawn_command() + cmd

            self._server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env if not use_spawn else None,
            )
            proc = self._server_process  # local ref to avoid race with _stop_server

        # Wait for the server to be ready (outside the lock so other threads can proceed)
        timeout = 120
        start = time.time()
        while time.time() - start < timeout:
            # Check if the process died prematurely
            if proc.poll() is not None:
                returncode = proc.returncode
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace").strip()
                self._server_process = None
                error_msg = f"sd-server exited with code {returncode}"
                if stderr_output:
                    error_msg += f": {stderr_output}"
                raise RuntimeError(error_msg)
            if self._is_server_running():
                return
            time.sleep(0.5)

        raise RuntimeError("sd-server failed to start within 120 seconds")

    def _stop_server(self) -> None:
        """Stop the sd-server process."""
        with self._server_lock:
            if self._server_process is not None:
                try:
                    self._server_process.terminate()
                    self._server_process.wait(timeout=5)
                except Exception:
                    try:
                        self._server_process.kill()
                    except Exception:
                        pass
                self._server_process = None

    def _generate_via_server(self, prompt: str, output_file: str) -> str:
        """Generate an image using the sd-server HTTP API.

        Posts to /sdapi/v1/txt2img and decodes the base64 image response.
        """
        port = self._get_server_port()
        url = f"http://127.0.0.1:{port}/sdapi/v1/txt2img"

        width = self.get_setting("width", True, 512)
        height = self.get_setting("height", True, 512)
        steps = self.get_setting("steps", True, 20)
        cfg_scale = self.get_setting("cfg_scale", True, 7.0)
        seed = self.get_setting("seed", True, -1)
        sampling_method = self.get_setting("sampling_method", True, "euler_a")
        clip_skip = self.get_setting("clip_skip", True, -1)
        negative_prompt_template = self.get_setting("negative_prompt_template", True, "")
        positive_prompt_template = self.get_setting("positive_prompt_template", True, "[input]")

        prompt = positive_prompt_template.replace("[input]", prompt)

        negative_prompt = ""
        if negative_prompt_template:
            negative_prompt = negative_prompt_template.replace("[input]", prompt)

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "cfg_scale": float(cfg_scale),
            "seed": int(seed),
            "sampler_name": str(sampling_method),
        }

        if clip_skip > 0:
            payload["clip_skip"] = int(clip_skip)

        try:
            resp = requests.post(url, json=payload, timeout=600)
            resp.raise_for_status()
            data = resp.json()

            images = data.get("images", [])
            if not images:
                raise RuntimeError("sd-server returned no images")

            import base64
            image_bytes = base64.b64decode(images[0])
            with open(output_file, "wb") as f:
                f.write(image_bytes)

            return output_file

        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Could not connect to sd-server on port {port}. Is it running?")
        except requests.exceptions.Timeout:
            raise TimeoutError("Image generation timed out after 10 minutes")
        except Exception as e:
            raise RuntimeError(f"sd-server generation failed: {e}")

    # ── Image generation ────────────────────────────────────────────

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        """Generate an image using stable-diffusion.cpp.

        Args:
            prompt: The text prompt for image generation
            msg_uuid: Unique message identifier
            output_file: Path to save the generated image

        Returns:
            str: Local file path to the generated image
        """
        if output_file is None:
            output_file = os.path.join(self.cache_dir, f"{msg_uuid}.png")

        # Use server mode if enabled
        if self.get_setting("use_server", False, False):
            self._start_server()
            return self._generate_via_server(prompt, output_file)

        model = self._resolve_model_path(self.get_setting("model"))
        if not model or not os.path.exists(model):
            raise FileNotFoundError(f"Model not found: {model}")

        binary = self._get_binary_path()
        width = self.get_setting("width", True, 512)
        height = self.get_setting("height", True, 512)
        steps = self.get_setting("steps", True, 20)
        cfg_scale = self.get_setting("cfg_scale", True, 7.0)
        seed = self.get_setting("seed", True, -1)
        sampling_method = self.get_setting("sampling_method", True, "euler_a")
        clip_skip = self.get_setting("clip_skip", True, -1)
        negative_prompt_template = self.get_setting("negative_prompt_template", True, "")
        positive_prompt_template = self.get_setting("positive_prompt_template", True, "[input]")

        prompt = positive_prompt_template.replace("[input]", prompt)

        if negative_prompt_template:
            negative_prompt = negative_prompt_template.replace("[input]", prompt)

        cmd = [
            binary,
            "-m", model,
            "-p", prompt,
            "-o", output_file,
            "-W", str(int(width)),
            "-H", str(int(height)),
            "--steps", str(int(steps)),
            "--cfg-scale", str(float(cfg_scale)),
            "-s", str(int(seed)),
            "--sampling-method", str(sampling_method),
        ]

        if clip_skip > 0:
            cmd.extend(["--clip-skip", str(int(clip_skip))])

        if self._is_lora_enabled():
            cmd.extend(["--lora-model-dir", self._get_lora_dir()])

        if negative_prompt_template:
            cmd.extend(["-n", negative_prompt])

        # Use flatpak-spawn for built binaries in Flatpak, and also for system binary
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        use_spawn = is_flatpak() and (use_system or (self._is_binary_installed() and self.get_setting("gpu_acceleration", False, False)))
        if use_spawn:
            cmd = get_spawn_command() + cmd

        env = os.environ.copy()
        # Add the binary's directory to LD_LIBRARY_PATH so it finds libstable-diffusion.so
        if binary == self.sd_binary_path:
            bin_dir = os.path.dirname(binary)
            if use_spawn:
                # flatpak-spawn doesn't inherit env vars; pass via --env= flags
                cmd = cmd[:1] + [f"--env=LD_LIBRARY_PATH={bin_dir}"] + cmd[1:]
            else:
                existing = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = bin_dir if not existing else f"{bin_dir}:{existing}"

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
            if result.returncode != 0:
                print(f"sd-cli error: {result.stderr}")
                raise RuntimeError(f"sd-cli failed: {result.stderr}")

            if not os.path.exists(output_file):
                # sd-cli may append .png automatically
                alt_output = output_file
                if not alt_output.endswith(".png"):
                    alt_output = output_file + ".png"
                if os.path.exists(alt_output):
                    return alt_output
                raise FileNotFoundError(f"Output file not created: {output_file}")

            return output_file

        except subprocess.TimeoutExpired:
            raise TimeoutError("Image generation timed out after 10 minutes")
        except Exception as e:
            raise RuntimeError(f"Image generation failed: {e}")

    # ── Installation dialog ────────────────────────────────────────────

    def show_install_dialog(self, button):
        win = Adw.Window(title="Install stable-diffusion.cpp")
        win.set_default_size(700, 620)
        win.set_modal(True)
        try:
            root = button.get_root()
            if root:
                win.set_transient_for(root)
        except Exception:
            pass

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(main_box)

        dots = Adw.CarouselIndicatorDots()
        dots.set_margin_top(12)
        main_box.append(dots)

        content = Adw.Carousel()
        content.set_allow_mouse_drag(False)
        content.set_allow_scroll_wheel(False)
        content.set_hexpand(True)
        content.set_vexpand(True)
        dots.set_carousel(content)
        main_box.append(content)

        # ── Page 0: Choose Method ──────────────────────────────────────

        page0 = Adw.StatusPage(
            title="Choose Installation Method",
            description="How would you like to install stable-diffusion.cpp?",
            icon_name="system-software-install-symbolic",
        )
        page0_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True, vexpand=True)
        page0_box.set_margin_start(24)
        page0_box.set_margin_end(24)
        page0_box.set_valign(Gtk.Align.CENTER)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20, homogeneous=True)
        cards_box.set_halign(Gtk.Align.CENTER)

        # Left card: Compile
        compile_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_card.add_css_class("card")
        compile_card.set_margin_top(8)
        compile_card.set_margin_bottom(8)
        compile_card.set_margin_start(12)
        compile_card.set_margin_end(12)

        compile_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_inner.set_margin_top(16)
        compile_inner.set_margin_bottom(16)
        compile_inner.set_margin_start(16)
        compile_inner.set_margin_end(16)
        compile_card.append(compile_inner)

        compile_icon = Gtk.Image.new_from_icon_name("tools-symbolic")
        compile_icon.set_pixel_size(48)
        compile_icon.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_icon)

        compile_title = Gtk.Label(label="Compile from Source")
        compile_title.add_css_class("title-4")
        compile_title.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_title)

        for text in [
            "Optimized for your specific hardware",
            "Full customization via CMake flags",
            "Supports CUDA, Vulkan, ROCm",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            compile_inner.append(lbl)

        for text in [
            "Takes 5-20 minutes to build",
            "Requires build tools (cmake, gcc)",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            compile_inner.append(lbl)

        btn_compile = Gtk.Button(label="Compile")
        btn_compile.add_css_class("suggested-action")
        btn_compile.set_halign(Gtk.Align.CENTER)
        btn_compile.set_margin_top(8)
        btn_compile.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        compile_inner.append(btn_compile)
        cards_box.append(compile_card)

        # Right card: Download
        download_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_card.add_css_class("card")
        download_card.set_margin_top(8)
        download_card.set_margin_bottom(8)
        download_card.set_margin_start(12)
        download_card.set_margin_end(12)

        download_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_inner.set_margin_top(16)
        download_inner.set_margin_bottom(16)
        download_inner.set_margin_start(16)
        download_inner.set_margin_end(16)
        download_card.append(download_inner)

        download_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        download_icon.set_pixel_size(48)
        download_icon.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_icon)

        download_title = Gtk.Label(label="Download Pre-built")
        download_title.add_css_class("title-4")
        download_title.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_title)

        for text in [
            "Ready in under a minute",
            "No build tools required",
            "Pre-tested official binaries",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            download_inner.append(lbl)

        for text in [
            "Generic CPU optimizations",
            "Limited to available releases",
            "No CUDA prebuilt for Linux",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            download_inner.append(lbl)

        btn_download = Gtk.Button(label="Download")
        btn_download.add_css_class("suggested-action")
        btn_download.set_halign(Gtk.Align.CENTER)
        btn_download.set_margin_top(8)
        btn_download.connect("clicked", lambda x: self._on_prebuilt_selected(content))
        download_inner.append(btn_download)
        cards_box.append(download_card)

        page0_box.append(cards_box)

        btn_cancel0 = Gtk.Button(label="Cancel")
        btn_cancel0.add_css_class("destructive-action")
        btn_cancel0.set_halign(Gtk.Align.CENTER)
        btn_cancel0.connect("clicked", lambda x: win.close())
        page0_box.append(btn_cancel0)

        page0.set_child(page0_box)
        content.append(page0)

        # ── Page 1: Hardware Selection (Compile path) ──────────────────

        page1 = Adw.StatusPage(
            title="Select Hardware",
            description="Choose your acceleration backend",
            icon_name="brain-augemnted-symbolic",
        )
        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        main_container.set_halign(Gtk.Align.CENTER)

        if not can_escape_sandbox():
            warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            warning_box.set_margin_bottom(16)
            warning_box.add_css_class("warning")

            warning_label = Gtk.Label(label="Flatpak Sandbox Warning")
            warning_label.add_css_class("heading")
            warning_label.set_halign(Gtk.Align.CENTER)
            warning_box.append(warning_label)

            warning_text = Gtk.Label(
                label="To build stable-diffusion.cpp with hardware acceleration in Flatpak,\n"
                      "you need to grant sandbox escape permissions.\n"
                      "Run the following command in a terminal:"
            )
            warning_text.set_halign(Gtk.Align.CENTER)
            warning_text.set_wrap(True)
            warning_box.append(warning_text)

            command_entry = Gtk.Entry()
            command_entry.set_text(
                "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle"
            )
            command_entry.set_editable(False)
            command_entry.set_halign(Gtk.Align.CENTER)
            command_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            command_box.set_halign(Gtk.Align.CENTER)
            command_box.append(command_entry)
            warning_box.append(command_box)

            copy_btn = Gtk.Button(label="Copy Command")
            copy_btn.set_halign(Gtk.Align.CENTER)
            copy_btn.connect(
                "clicked",
                lambda btn: self._copy_to_clipboard(
                    "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle"
                ),
            )
            warning_box.append(copy_btn)

            main_container.append(warning_box)
            main_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24, hexpand=True)
        hbox.set_halign(Gtk.Align.CENTER)
        hbox.set_margin_start(24)
        hbox.set_margin_end(24)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.hw_options = {}
        group = None
        for hw in ["CPU", "CPU (OpenBLAS)", "Nvidia (CUDA)", "AMD (ROCm)", "Any GPU (Vulkan)"]:
            btn = Gtk.CheckButton(label=hw, group=group)
            if group is None:
                group = btn
                btn.set_active(True)
            self.hw_options[hw] = btn
            left_box.append(btn)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        lbl_flags = Gtk.Label(label="Custom CMake Flags (Optional)")
        lbl_flags.set_halign(Gtk.Align.START)
        right_box.append(lbl_flags)

        self.entry_cmake = Gtk.Entry()
        self.entry_cmake.set_placeholder_text("-DSD_WEBP=OFF ...")
        right_box.append(self.entry_cmake)

        hbox.append(left_box)
        hbox.append(right_box)
        main_container.append(hbox)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)

        btn_cancel1 = Gtk.Button(label="Cancel")
        btn_cancel1.add_css_class("destructive-action")
        btn_cancel1.connect("clicked", lambda x: win.close())
        button_box.append(btn_cancel1)

        btn_next1 = Gtk.Button(label="Next")
        if not can_escape_sandbox():
            btn_next1.set_sensitive(False)
            btn_next1.set_tooltip_text("Please run the Flatpak override command first")
        else:
            btn_next1.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(3), True))
        button_box.append(btn_next1)

        main_container.append(button_box)

        page1.set_child(main_container)
        content.append(page1)

        # ── Page 2: Pre-built Binary Selection ─────────────────────────

        page2 = Adw.StatusPage(
            title="Select Pre-built Binary",
            description="Choose the binary that matches your hardware",
            icon_name="folder-download-symbolic",
        )
        self.prebuilt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        self.prebuilt_box.set_margin_start(24)
        self.prebuilt_box.set_margin_end(24)
        self.prebuilt_box.set_halign(Gtk.Align.CENTER)

        self.prebuilt_spinner = Gtk.Spinner()
        self.prebuilt_spinner.set_halign(Gtk.Align.CENTER)
        self.prebuilt_spinner.start()
        self.prebuilt_box.append(self.prebuilt_spinner)

        self.prebuilt_list_box = Gtk.ListBox()
        self.prebuilt_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.prebuilt_list_box.add_css_class("boxed-list")
        self.prebuilt_list_box.set_hexpand(True)
        self.prebuilt_box.append(self.prebuilt_list_box)

        self.prebuilt_error_label = Gtk.Label(label="")
        self.prebuilt_error_label.set_halign(Gtk.Align.CENTER)
        self.prebuilt_error_label.set_wrap(True)
        self.prebuilt_box.append(self.prebuilt_error_label)

        prebuilt_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        prebuilt_buttons.set_halign(Gtk.Align.CENTER)
        prebuilt_buttons.set_margin_top(12)

        btn_back_prebuilt = Gtk.Button(label="Back")
        btn_back_prebuilt.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(0), True))
        prebuilt_buttons.append(btn_back_prebuilt)

        self.btn_start_prebuilt = Gtk.Button(label="Download & Install")
        self.btn_start_prebuilt.add_css_class("suggested-action")
        self.btn_start_prebuilt.set_sensitive(False)
        self.btn_start_prebuilt.connect("clicked", lambda x: self._start_prebuilt_install(content))
        prebuilt_buttons.append(self.btn_start_prebuilt)

        self.prebuilt_box.append(prebuilt_buttons)
        page2.set_child(self.prebuilt_box)
        content.append(page2)

        # ── Page 3: Ready to Build ─────────────────────────────────────

        page3 = Adw.StatusPage(
            title="Ready to Build",
            description="Click start to begin compilation",
            icon_name="tools-symbolic",
        )
        box3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box3.set_halign(Gtk.Align.CENTER)

        btn_start = Gtk.Button(label="Start Build")
        btn_start.set_halign(Gtk.Align.CENTER)
        btn_start.connect("clicked", lambda x: self._start_build(content))
        box3.append(btn_start)

        btn_back3 = Gtk.Button(label="Back")
        btn_back3.set_halign(Gtk.Align.CENTER)
        btn_back3.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        box3.append(btn_back3)

        page3.set_child(box3)
        content.append(page3)

        # ── Page 4: Progress ───────────────────────────────────────────

        page4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page4.set_hexpand(True)
        page4.set_vexpand(True)
        page4.set_margin_top(24)
        page4.set_margin_bottom(24)
        page4.set_margin_start(24)
        page4.set_margin_end(24)

        icon4 = Gtk.Image.new_from_icon_name("magic-wand-symbolic")
        icon4.set_pixel_size(96)
        icon4.set_halign(Gtk.Align.CENTER)
        page4.append(icon4)

        title4 = Gtk.Label(label="Installing")
        title4.add_css_class("title-1")
        title4.set_halign(Gtk.Align.CENTER)
        page4.append(title4)

        desc4 = Gtk.Label(label="Please wait...")
        desc4.set_halign(Gtk.Align.CENTER)
        page4.append(desc4)

        self.progress_bar = Gtk.ProgressBar()
        page4.append(self.progress_bar)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.log_view)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        page4.append(scroll)
        content.append(page4)

        # ── Page 5: Done ───────────────────────────────────────────────

        page5 = Adw.StatusPage(
            title="Completed",
            description="Installation finished successfully",
            icon_name="emblem-default-symbolic",
        )
        btn_close = Gtk.Button(label="Close")
        btn_close.set_halign(Gtk.Align.CENTER)
        btn_close.connect("clicked", lambda x: self._finish_install(win))
        page5.set_child(btn_close)
        content.append(page5)

        win.present()

    # ── Prebuilt binary download ───────────────────────────────────────

    def _on_prebuilt_selected(self, content):
        content.scroll_to(content.get_nth_page(2), True)
        if not hasattr(self, "_prebuilt_fetched") or not self._prebuilt_fetched:
            self._prebuilt_fetched = True
            threading.Thread(target=self._fetch_prebuilt_releases, args=(content,), daemon=True).start()

    @staticmethod
    def _detect_arch():
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            return "x86_64"
        elif machine in ("aarch64", "arm64"):
            return "arm64"
        return machine

    @staticmethod
    def _parse_asset_backend(name):
        name_lower = name.lower()
        if "rocm" in name_lower:
            return "rocm"
        elif "vulkan" in name_lower:
            return "vulkan"
        return "cpu"

    @staticmethod
    def _human_size(size_bytes):
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    @staticmethod
    def _backend_display_name(backend):
        return {
            "cpu": "CPU (Basic)",
            "vulkan": "Any GPU (Vulkan)",
            "rocm": "AMD ROCm",
        }.get(backend, backend)

    def _fetch_prebuilt_releases(self, carousel):
        arch = self._detect_arch()
        available = []

        try:
            # Use the specific release tagged in the repo
            resp = requests.get(self.RELEASE_API_URL + "/latest", timeout=15)
            resp.raise_for_status()
            release = resp.json()
            tag = release.get("tag_name", "unknown")

            for asset in release.get("assets", []):
                name = asset["name"]
                url = asset["browser_download_url"]
                size = asset.get("size", 0)

                # We only support Linux .zip archives
                if not name.endswith(".zip"):
                    continue
                if "Linux" not in name:
                    continue
                if "Darwin" in name or "macOS" in name:
                    continue

                # Check architecture match
                if arch == "x86_64" and ("arm64" in name.lower() or "aarch64" in name.lower()):
                    continue
                if arch == "arm64" and "x86_64" in name and "arm64" not in name.lower():
                    continue

                backend = self._parse_asset_backend(name)
                available.append({
                    "name": name,
                    "url": url,
                    "size": size,
                    "backend": backend,
                    "tag": tag,
                })

        except Exception as e:
            GLib.idle_add(self._show_prebuilt_error, f"Failed to fetch releases: {e}")
            return

        if not available:
            GLib.idle_add(
                self._show_prebuilt_error,
                f"No compatible pre-built binaries found for your architecture ({arch}).",
            )
            return

        # Sort: compatible backends first
        backend_checks = {}
        for b in ("vulkan", "rocm"):
            backend_checks[b] = has_backend(b)

        for item in available:
            item["compatible"] = item["backend"] == "cpu" or backend_checks.get(item["backend"], False)

        def _sort_key(x):
            is_compatible = 0 if x["compatible"] else 1
            backend_prio = {"vulkan": 0, "rocm": 1, "cpu": 2}.get(x["backend"], 99)
            return (is_compatible, backend_prio)

        available.sort(key=_sort_key)

        GLib.idle_add(self._populate_prebuilt_list, available)

    def _show_prebuilt_error(self, message):
        if hasattr(self, "prebuilt_spinner") and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)
        self.prebuilt_error_label.set_text(message)

    def _populate_prebuilt_list(self, available):
        if hasattr(self, "prebuilt_spinner") and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)

        child = self.prebuilt_list_box.get_first_child()
        while child:
            self.prebuilt_list_box.remove(child)
            child = self.prebuilt_list_box.get_first_child()

        self.prebuilt_assets = available
        group = None
        first_recommended = None
        first_overall = None

        for i, item in enumerate(available):
            row = Adw.ActionRow()
            row.set_title(self._backend_display_name(item["backend"]))

            if item["compatible"] and item["backend"] != "cpu":
                rec = Gtk.Label(label="Recommended")
                rec.add_css_class("success")
                rec.add_css_class("caption")
                rec.set_valign(Gtk.Align.CENTER)
                row.add_suffix(rec)

            subtitle_parts = [
                self._human_size(item["size"]),
                item["tag"],
            ]
            row.set_subtitle("  |  ".join(subtitle_parts))

            check = Gtk.CheckButton()
            if group is None:
                group = check
                check.set_active(True)
            else:
                check.set_group(group)
            row.add_prefix(check)
            row.set_activatable_widget(check)

            if item["compatible"] and first_recommended is None:
                first_recommended = i
                check.set_active(True)
            if first_overall is None:
                first_overall = i

            self.prebuilt_list_box.append(row)

        self._selected_prebuilt = first_recommended if first_recommended is not None else first_overall
        self.btn_start_prebuilt.set_sensitive(True)

        def on_row_activated(listbox, row):
            idx = 0
            child = listbox.get_first_child()
            while child:
                if child == row:
                    break
                child = child.get_next_sibling()
                idx += 1
            self._selected_prebuilt = idx

        self.prebuilt_list_box.connect("row-activated", on_row_activated)

    def _start_prebuilt_install(self, carousel):
        if not hasattr(self, "_selected_prebuilt") or self._selected_prebuilt is None:
            return
        if not hasattr(self, "prebuilt_assets") or self._selected_prebuilt >= len(self.prebuilt_assets):
            return

        asset = self.prebuilt_assets[self._selected_prebuilt]
        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self._run_prebuilt_install, args=(asset, carousel), daemon=True).start()

    def _run_prebuilt_install(self, asset, carousel):
        def append_log(text):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text)
            return False

        def set_progress(fraction):
            self.progress_bar.set_fraction(fraction)
            return False

        try:
            GLib.idle_add(append_log, f"Downloading {asset['name']}...\n")
            GLib.idle_add(set_progress, 0.0)

            resp = requests.get(asset["url"], stream=True, timeout=300)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", asset["size"]))
            downloaded = 0

            tmp_dir = tempfile.mkdtemp()
            tmp_file = os.path.join(tmp_dir, asset["name"])

            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress = (downloaded / total) * 0.7
                        GLib.idle_add(set_progress, progress)

            GLib.idle_add(set_progress, 0.7)
            GLib.idle_add(append_log, "Download complete. Extracting...\n")

            abs_sd_path = os.path.abspath(self.sd_cpp_path)
            if os.path.exists(abs_sd_path):
                shutil.rmtree(abs_sd_path)

            with zipfile.ZipFile(tmp_file, "r") as zf:
                zf.extractall(tmp_dir)

            # Find the extracted directory or binary
            extracted_items = os.listdir(tmp_dir)
            extracted_items = [i for i in extracted_items if i != asset["name"]]

            build_bin = os.path.join(abs_sd_path, "build", "bin")
            os.makedirs(build_bin, exist_ok=True)

            # Look for sd-cli and sd-server binaries in extracted files
            sd_binary_found = False
            for root, _, files in os.walk(tmp_dir):
                for f in files:
                    if f == "sd-cli" or f == "sd":
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, "sd-cli")
                        shutil.move(src, dst)
                        os.chmod(dst, 0o755)
                        sd_binary_found = True
                    elif f == "sd-server":
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, "sd-server")
                        shutil.move(src, dst)
                        os.chmod(dst, 0o755)

            # Also copy any .so files
            for root, _, files in os.walk(tmp_dir):
                for f in files:
                    if f.endswith(".so") or f.endswith(".so.*"):
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, f)
                        shutil.move(src, dst)

            if not sd_binary_found:
                raise Exception("sd-cli binary not found in the archive")

            shutil.rmtree(tmp_dir, ignore_errors=True)

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Installation completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", asset.get("backend") != "cpu")

        except Exception as e:
            GLib.idle_add(append_log, f"\nError: {e}\n")
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())

    # ── Build from source ──────────────────────────────────────────────

    def _start_build(self, carousel):
        backend = "cpu"
        if self.hw_options["Nvidia (CUDA)"].get_active():
            backend = "cuda"
        elif self.hw_options["AMD (ROCm)"].get_active():
            backend = "rocm"
        elif self.hw_options["Any GPU (Vulkan)"].get_active():
            backend = "vulkan"
        elif self.hw_options["CPU (OpenBLAS)"].get_active():
            backend = "cpu_openblas"

        custom_flags = self.entry_cmake.get_text()

        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self._run_build, args=(backend, carousel, custom_flags), daemon=True).start()

    def _run_build(self, backend, carousel, custom_flags=""):
        if not can_escape_sandbox():
            self.throw("You have to escape the sandbox to build stable-diffusion.cpp", ErrorSeverity.ERROR)
            return

        try:
            env = os.environ.copy()
            cmake_args = ["-DCMAKE_BUILD_TYPE=Release"]

            if backend == "cuda":
                cmake_args.append("-DSD_CUDA=ON")
            elif backend == "rocm":
                cmake_args.append("-DSD_HIPBLAS=ON")
            elif backend == "vulkan":
                cmake_args.append("-DSD_VULKAN=ON")
            elif backend == "cpu_openblas":
                cmake_args.append("-DGGML_OPENBLAS=ON")

            if custom_flags:
                custom_list = custom_flags.split() if isinstance(custom_flags, str) else custom_flags
                cmake_args.extend(custom_list)

            def append_log(text):
                buffer = self.log_view.get_buffer()
                buffer.insert(buffer.get_end_iter(), text)
                return False

            def set_progress(fraction):
                self.progress_bar.set_fraction(fraction)
                return False

            def run_cmd(cmd_list, extra_env=None, cwd=None):
                full_cmd = cmd_list
                if is_flatpak():
                    flatpak_cmd = get_spawn_command()
                    if extra_env:
                        for k, v in extra_env.items():
                            flatpak_cmd.extend([f"--env={k}={v}"])
                    full_cmd = flatpak_cmd + cmd_list
                else:
                    if extra_env:
                        env.update(extra_env)

                process = subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env if not is_flatpak() else None,
                    cwd=cwd,
                )

                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        GLib.idle_add(append_log, line)
                return process.poll() == 0

            GLib.idle_add(set_progress, 0.1)
            GLib.idle_add(append_log, "Cloning stable-diffusion.cpp repository...\n")

            abs_sd_path = os.path.abspath(self.sd_cpp_path)

            if os.path.exists(abs_sd_path):
                run_cmd(["rm", "-rf", abs_sd_path])

            clone_cmd = ["git", "clone", "--depth", "1", self.REPO_URL, abs_sd_path]
            if not run_cmd(clone_cmd):
                raise Exception("Failed to clone stable-diffusion.cpp repository")

            # Initialize submodules (for webp/webm dependencies)
            GLib.idle_add(set_progress, 0.15)
            GLib.idle_add(append_log, "Initializing submodules...\n")
            submodule_cmd = ["git", "submodule", "update", "--init", "--recursive"]
            if not run_cmd(submodule_cmd, cwd=abs_sd_path):
                GLib.idle_add(append_log, "Warning: submodule init failed, continuing anyway...\n")

            GLib.idle_add(set_progress, 0.25)
            GLib.idle_add(append_log, "Configuring CMake build...\n")

            cmake_configure = ["cmake", "-B", "build"] + cmake_args
            if not run_cmd(cmake_configure, cwd=abs_sd_path):
                raise Exception("Failed to configure CMake build")

            GLib.idle_add(set_progress, 0.4)
            GLib.idle_add(append_log, f"Building stable-diffusion.cpp (Backend: {backend})...\n")
            GLib.idle_add(append_log, "This may take several minutes...\n")

            import multiprocessing
            num_jobs = multiprocessing.cpu_count()
            cmake_build = ["cmake", "--build", "build", "--config", "Release", "-j", str(num_jobs)]
            if not run_cmd(cmake_build, cwd=abs_sd_path):
                raise Exception("Failed to build stable-diffusion.cpp")

            sd_binary = os.path.join(abs_sd_path, "build", "bin", "sd-cli")
            if not os.path.exists(sd_binary):
                # Check for sd binary name
                sd_alt = os.path.join(abs_sd_path, "build", "bin", "sd")
                if os.path.exists(sd_alt):
                    os.rename(sd_alt, sd_binary)

            sd_server = os.path.join(abs_sd_path, "build", "bin", "sd-server")
            if not os.path.exists(sd_binary) and not os.path.exists(sd_server):
                raise Exception("sd-cli and sd-server binaries not found after build")

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Build completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", backend != "cpu")

        except Exception as e:
            GLib.idle_add(
                append_log,
                f"\nError: {e}\n",
            )
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())

    def _finish_install(self, win):
        win.close()
        self.settings_update()

    def _copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
