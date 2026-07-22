"""Catalog-driven setup window for MCP applications."""

from __future__ import annotations

import gettext
import ipaddress
import json
import os
import re
import shlex
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from ..utility.system import can_escape_sandbox, is_flatpak, open_website


_ = gettext.gettext

CATALOG_SCHEMA_VERSION = 1
CATALOG_RESOURCE = "/io/github/qwersyk/Newelle/mcp_servers.json"
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/qwersyk/Newelle/master/data/mcp_servers.json"
)
MAX_CATALOG_BYTES = 1024 * 1024
MAX_LOGO_BYTES = 512 * 1024
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z][A-Za-z0-9_]*)\}")
FIELD_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_FIELD_TYPES = {"text", "secret", "url", "file", "directory"}
ALLOWED_AUTH_TYPES = {"none", "bearer", "oauth"}
ALLOWED_AUTH_PARAMS = {"access_type", "prompt", "login_hint", "include_granted_scopes"}
LOGO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-catalog-logo")


class CatalogValidationError(ValueError):
    """Raised when a local or remote catalog is not safe to consume."""


def _required_string(value, label, max_length=4096):
    if not isinstance(value, str) or not value.strip():
        raise CatalogValidationError(f"{label} must be a non-empty string")
    if len(value) > max_length or "\x00" in value:
        raise CatalogValidationError(f"{label} is too long or contains invalid data")
    return value


def _optional_string(value, label, max_length=4096):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length or "\x00" in value:
        raise CatalogValidationError(f"{label} must be a valid string")
    return value


def _validate_https_url(value, label):
    value = _required_string(value, label)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CatalogValidationError(f"{label} must use HTTPS")


def _validate_http_endpoint(value, label):
    value = _required_string(value, label)
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if (
        not PLACEHOLDER_RE.search(value)
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise CatalogValidationError(f"{label} must use HTTPS or a loopback HTTP address")


def _walk_strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, path + (index,))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, path + (key,))


def validate_catalog(payload):
    """Validate and return a catalog payload.

    Catalogs may be downloaded, so validation deliberately accepts only data
    that maps to the MCP transport primitives Newelle already supports. It
    never accepts shell snippets or setup commands.
    """
    if not isinstance(payload, dict):
        raise CatalogValidationError("catalog must be a JSON object")
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogValidationError("unsupported catalog schema version")
    applications = payload.get("applications")
    if not isinstance(applications, list) or not applications:
        raise CatalogValidationError("catalog applications must be a non-empty list")
    if len(applications) > 200:
        raise CatalogValidationError("catalog contains too many applications")

    application_ids = set()
    for index, application in enumerate(applications):
        prefix = f"applications[{index}]"
        if not isinstance(application, dict):
            raise CatalogValidationError(f"{prefix} must be an object")

        app_id = _required_string(application.get("id"), f"{prefix}.id", 64)
        if not FIELD_ID_RE.fullmatch(app_id) or app_id in application_ids:
            raise CatalogValidationError(f"{prefix}.id is invalid or duplicated")
        application_ids.add(app_id)
        _required_string(application.get("name"), f"{prefix}.name", 120)
        _required_string(application.get("description"), f"{prefix}.description", 500)
        _validate_https_url(application.get("logo_url"), f"{prefix}.logo_url")

        setup = application.get("setup", {})
        if not isinstance(setup, dict):
            raise CatalogValidationError(f"{prefix}.setup must be an object")
        _optional_string(setup.get("instructions"), f"{prefix}.setup.instructions", 2000)
        if setup.get("help_url") is not None:
            _validate_https_url(setup["help_url"], f"{prefix}.setup.help_url")

        fields = setup.get("fields", [])
        if not isinstance(fields, list) or len(fields) > 20:
            raise CatalogValidationError(f"{prefix}.setup.fields must be a list")
        field_ids = set()
        secret_fields = set()
        for field_index, field in enumerate(fields):
            field_prefix = f"{prefix}.setup.fields[{field_index}]"
            if not isinstance(field, dict):
                raise CatalogValidationError(f"{field_prefix} must be an object")
            field_id = _required_string(field.get("id"), f"{field_prefix}.id", 64)
            if not FIELD_ID_RE.fullmatch(field_id) or field_id in field_ids:
                raise CatalogValidationError(f"{field_prefix}.id is invalid or duplicated")
            field_ids.add(field_id)
            field_type = field.get("type", "text")
            if field_type not in ALLOWED_FIELD_TYPES:
                raise CatalogValidationError(f"{field_prefix}.type is unsupported")
            if field_type == "secret":
                secret_fields.add(field_id)
            _required_string(field.get("label"), f"{field_prefix}.label", 120)
            _optional_string(field.get("description"), f"{field_prefix}.description", 300)
            _optional_string(field.get("placeholder"), f"{field_prefix}.placeholder", 300)
            _optional_string(field.get("default"), f"{field_prefix}.default", 1000)
            if "required" in field and not isinstance(field["required"], bool):
                raise CatalogValidationError(f"{field_prefix}.required must be a boolean")

        server = application.get("server")
        if not isinstance(server, dict):
            raise CatalogValidationError(f"{prefix}.server must be an object")
        server_type = server.get("type")
        if server_type not in {"http", "stdio"}:
            raise CatalogValidationError(f"{prefix}.server.type is unsupported")
        _optional_string(server.get("title"), f"{prefix}.server.title", 120)

        if server_type == "http":
            _validate_http_endpoint(server.get("url"), f"{prefix}.server.url")
            headers = server.get("headers", {})
            if not isinstance(headers, dict) or len(headers) > 30:
                raise CatalogValidationError(f"{prefix}.server.headers must be an object")
            for key, value in headers.items():
                _required_string(key, f"{prefix}.server.headers key", 200)
                if not isinstance(value, str):
                    raise CatalogValidationError(
                        f"{prefix}.server.headers.{key} must be a string"
                    )
                _optional_string(value, f"{prefix}.server.headers.{key}", 4000)

            auth = server.get("auth", {"type": "none"})
            if not isinstance(auth, dict) or auth.get("type", "none") not in ALLOWED_AUTH_TYPES:
                raise CatalogValidationError(f"{prefix}.server.auth is unsupported")
            auth_type = auth.get("type", "none")
            if auth_type == "bearer":
                _required_string(auth.get("token"), f"{prefix}.server.auth.token", 4000)
            elif auth_type == "oauth":
                for key in ("client_id", "client_secret"):
                    _optional_string(auth.get(key), f"{prefix}.server.auth.{key}", 4000)
                redirect_port = auth.get("redirect_port")
                if redirect_port is not None and (
                    not isinstance(redirect_port, int) or not 1024 <= redirect_port <= 65535
                ):
                    raise CatalogValidationError(f"{prefix}.server.auth.redirect_port is invalid")
                scopes = auth.get("scopes")
                if scopes is not None and (
                    not isinstance(scopes, list)
                    or not scopes
                    or any(not isinstance(scope, str) or not scope for scope in scopes)
                ):
                    raise CatalogValidationError(f"{prefix}.server.auth.scopes is invalid")
                method = auth.get("token_endpoint_auth_method")
                if method not in {None, "client_secret_basic", "client_secret_post", "none"}:
                    raise CatalogValidationError(
                        f"{prefix}.server.auth.token_endpoint_auth_method is invalid"
                    )
                params = auth.get("authorization_params", {})
                if not isinstance(params, dict) or any(
                    key not in ALLOWED_AUTH_PARAMS or not isinstance(value, str)
                    for key, value in params.items()
                ):
                    raise CatalogValidationError(
                        f"{prefix}.server.auth.authorization_params is invalid"
                    )
        else:
            _required_string(server.get("command"), f"{prefix}.server.command", 1000)
            args = server.get("args", [])
            env = server.get("env", {})
            if not isinstance(args, list) or len(args) > 100 or any(
                not isinstance(arg, str) or "\x00" in arg or len(arg) > 4000 for arg in args
            ):
                raise CatalogValidationError(f"{prefix}.server.args must be a string list")
            if not isinstance(env, dict) or len(env) > 100:
                raise CatalogValidationError(f"{prefix}.server.env must be an object")
            for key, value in env.items():
                if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
                    raise CatalogValidationError(f"{prefix}.server.env contains an invalid key")
                if not isinstance(value, str):
                    raise CatalogValidationError(f"{prefix}.server.env.{key} must be a string")
                _optional_string(value, f"{prefix}.server.env.{key}", 4000)

        used_fields = set()
        for path, string in _walk_strings(server):
            placeholders = set(PLACEHOLDER_RE.findall(string))
            used_fields.update(placeholders)
            for secret_field in placeholders & secret_fields:
                is_allowed_secret_sink = (
                    path in {("auth", "token"), ("auth", "client_secret")}
                    or (len(path) == 2 and path[0] in {"headers", "env"})
                )
                if not is_allowed_secret_sink:
                    raise CatalogValidationError(
                        f"{prefix} uses secret field {secret_field} in an unsafe location"
                    )
        unknown_fields = used_fields - field_ids
        if unknown_fields:
            raise CatalogValidationError(
                f"{prefix} references undeclared fields: {', '.join(sorted(unknown_fields))}"
            )

    return payload


def _render_templates(value, values):
    if isinstance(value, str):
        def replace(match):
            field_id = match.group(1)
            if field_id not in values:
                raise CatalogValidationError(f"missing setup value: {field_id}")
            return values[field_id]

        return PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [_render_templates(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _render_templates(item, values) for key, item in value.items()}
    return value


def _catalog_text(value):
    """Translate bundled catalog copy while leaving remote copy extensible."""
    return _(value) if value else value


def _validate_rendered_server(server):
    """Revalidate a server after user values have replaced its placeholders."""
    validate_catalog(
        {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "applications": [
                {
                    "id": "rendered_server",
                    "name": "Rendered MCP server",
                    "description": "Rendered MCP server configuration",
                    "logo_url": "https://example.com/logo.png",
                    "setup": {},
                    "server": server,
                }
            ],
        }
    )
    return server


def _load_json_bytes(raw):
    try:
        return validate_catalog(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogValidationError("catalog is not valid UTF-8 JSON") from exc


def load_bundled_catalog():
    """Load the catalog resource, with a source-tree fallback for development."""
    try:
        resource = Gio.resources_lookup_data(
            CATALOG_RESOURCE, Gio.ResourceLookupFlags.NONE
        )
        return _load_json_bytes(bytes(resource.get_data()))
    except GLib.Error:
        source_path = Path(__file__).resolve().parents[2] / "data" / "mcp_servers.json"
        with source_path.open("rb") as catalog_file:
            return _load_json_bytes(catalog_file.read(MAX_CATALOG_BYTES + 1))


def fetch_remote_catalog(url):
    """Download and validate an HTTPS catalog with strict size and time limits."""
    _validate_https_url(url, "catalog URL")
    chunks = []
    size = 0
    with requests.get(url, stream=True, timeout=(5, 15)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(16 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_CATALOG_BYTES:
                raise CatalogValidationError("catalog download is too large")
            chunks.append(chunk)
    return _load_json_bytes(b"".join(chunks))


def _load_cached_catalog(path):
    try:
        with open(path, "rb") as catalog_file:
            raw = catalog_file.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            return None
        return _load_json_bytes(raw)
    except (OSError, CatalogValidationError):
        return None


def _save_cached_catalog(path, catalog):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as catalog_file:
        json.dump(catalog, catalog_file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, path)


def _load_logo(url, callback):
    """Load a small remote logo without allowing an unbounded image download."""
    def validate_target(target):
        parsed = urlparse(target)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CatalogValidationError("logo URL must be a public HTTPS URL")
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        if not addresses or any(
            not ipaddress.ip_address(address[4][0]).is_global for address in addresses
        ):
            raise CatalogValidationError("logo URL must resolve to a public address")

    def worker():
        pixbuf = None
        try:
            current_url = url
            response = None
            chunks = []
            size = 0
            for _redirect in range(4):
                validate_target(current_url)
                response = requests.get(
                    current_url,
                    stream=True,
                    timeout=(5, 15),
                    allow_redirects=False,
                )
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        return
                    current_url = urljoin(current_url, location)
                    continue
                break
            else:
                return

            with response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type and not content_type.startswith("image/"):
                    return
                for chunk in response.iter_content(8 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_LOGO_BYTES:
                        return
                    chunks.append(chunk)
            loader = GdkPixbuf.PixbufLoader()

            def size_prepared(current_loader, width, height):
                if width > 256 or height > 256:
                    scale = min(256 / max(width, 1), 256 / max(height, 1))
                    current_loader.set_size(
                        max(1, int(width * scale)), max(1, int(height * scale))
                    )

            loader.connect("size-prepared", size_prepared)
            loader.write(b"".join(chunks))
            loader.close()
            pixbuf = loader.get_pixbuf()
        except (
            CatalogValidationError,
            OSError,
            ValueError,
            requests.RequestException,
            GLib.Error,
        ):
            pass
        finally:
            GLib.idle_add(callback, pixbuf)

    LOGO_EXECUTOR.submit(worker)


class ConnectApplicationWindow(Adw.Window):
    """Browse a catalog and connect a guided HTTP or STDIO MCP server."""

    def __init__(self, parent, controller, on_connected=None, catalog_url=None):
        super().__init__()
        self.controller = controller
        self.on_connected = on_connected
        self.catalog_url = catalog_url or os.environ.get(
            "NEWELLE_MCP_CATALOG_URL", DEFAULT_CATALOG_URL
        )
        self.cache_path = os.path.join(controller.config_dir, "mcp_servers_catalog.json")
        self.catalog = _load_cached_catalog(self.cache_path) or load_bundled_catalog()
        self.application_rows = []
        self.field_entries = {}
        self.field_definitions = {}
        self.logo_cache = {}
        self.logo_waiters = {}
        self.selected_application = None
        self.setup_page = None
        self.connecting = False
        self.closed = False

        self.set_title(_("Connect Application"))
        self.set_default_size(720, 640)
        self.set_modal(True)
        self.set_transient_for(parent)
        self.connect("close-request", self._on_close_request)

        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=200,
        )
        self.toast_overlay.set_child(self.stack)
        self.set_content(self.toast_overlay)

        self._build_catalog_page()
        self._populate_catalog()
        self._refresh_remote_catalog(show_error=False)

    def _build_catalog_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        self.catalog_title = Adw.WindowTitle(
            title=_("Connect Application"),
            subtitle=_("Choose an application from the MCP catalog"),
        )
        header.set_title_widget(self.catalog_title)

        self.refresh_spinner = Gtk.Spinner()
        header.pack_end(self.refresh_spinner)
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text=_("Refresh application catalog"),
            css_classes=["flat"],
        )
        self.refresh_button.connect(
            "clicked", lambda _button: self._refresh_remote_catalog(show_error=True)
        )
        header.pack_end(self.refresh_button)
        page.append(header)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search applications"),
            margin_top=12,
            margin_bottom=6,
            margin_start=18,
            margin_end=18,
        )
        self.search_entry.connect("search-changed", lambda _entry: self._populate_catalog())
        page.append(self.search_entry)

        self.results_stack = Gtk.Stack()
        self.results_stack.set_vexpand(True)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=1000)
        catalog_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=12,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        self.catalog_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            row_spacing=12,
            column_spacing=12,
            min_children_per_line=1,
            max_children_per_line=4,
            valign=Gtk.Align.START,
        )
        catalog_box.append(self.catalog_flow)
        clamp.set_child(catalog_box)
        scroll.set_child(clamp)
        self.results_stack.add_named(scroll, "results")

        empty = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title=_("No applications found"),
            description=_("Try a different search."),
        )
        self.results_stack.add_named(empty, "empty")
        page.append(self.results_stack)

        self.stack.add_named(page, "catalog")
        self.stack.set_visible_child_name("catalog")

    def _populate_catalog(self):
        child = self.catalog_flow.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.catalog_flow.remove(child)
            child = next_child
        self.application_rows = []

        query = self.search_entry.get_text().strip().casefold()
        applications = [
            application
            for application in self.catalog["applications"]
            if not query
            or query in _catalog_text(application["name"]).casefold()
            or query in _catalog_text(application["description"]).casefold()
        ]

        for application in applications:
            card = Gtk.Button(
                hexpand=True,
                width_request=300,
                css_classes=["card"],
            )
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=12,
                margin_top=14,
                margin_bottom=14,
                margin_start=14,
                margin_end=14,
            )
            heading = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
            )
            heading.append(self._create_avatar(application, size=42))
            name = Gtk.Label(
                label=_catalog_text(application["name"]),
                xalign=0,
                hexpand=True,
                wrap=True,
                valign=Gtk.Align.CENTER,
            )
            name.add_css_class("heading")
            heading.append(name)

            if self._is_application_connected(application):
                connected = Gtk.Image(
                    icon_name="emblem-ok-symbolic",
                    tooltip_text=_("Connected"),
                    valign=Gtk.Align.CENTER,
                )
                connected.add_css_class("success")
                heading.append(connected)

            heading.append(
                Gtk.Image(
                    icon_name="go-next-symbolic",
                    valign=Gtk.Align.CENTER,
                )
            )
            content.append(heading)

            description = Gtk.Label(
                label=_catalog_text(application["description"]),
                xalign=0,
                yalign=0,
                wrap=True,
                max_width_chars=38,
                valign=Gtk.Align.START,
            )
            description.add_css_class("dim-label")
            content.append(description)

            card.set_child(content)
            card.connect("clicked", self._on_application_activated, application)
            self.catalog_flow.append(card)
            self.application_rows.append(card)

        self.results_stack.set_visible_child_name("results" if applications else "empty")

    def _create_avatar(self, application, size=46):
        avatar = Adw.Avatar(
            size=size,
            text=_catalog_text(application["name"]),
            show_initials=True,
        )
        logo_url = application["logo_url"]
        if logo_url in self.logo_cache:
            avatar.set_custom_image(self.logo_cache[logo_url])
            return avatar

        waiters = self.logo_waiters.setdefault(logo_url, [])
        waiters.append((avatar, size))
        if len(waiters) == 1:
            _load_logo(
                logo_url,
                lambda pixbuf, current_url=logo_url: self._finish_logo(
                    current_url, pixbuf
                ),
            )
        return avatar

    def _finish_logo(self, logo_url, pixbuf):
        waiters = self.logo_waiters.pop(logo_url, [])
        if self.closed or pixbuf is None or not waiters:
            return False
        target_size = max(size for _avatar, size in waiters)
        width = max(pixbuf.get_width(), 1)
        height = max(pixbuf.get_height(), 1)
        scale = min(target_size / width, target_size / height)
        scaled = pixbuf.scale_simple(
            max(1, int(width * scale)),
            max(1, int(height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        texture = Gdk.Texture.new_for_pixbuf(scaled or pixbuf)
        self.logo_cache[logo_url] = texture
        for avatar, _size in waiters:
            avatar.set_custom_image(texture)
        return False

    def _on_close_request(self, _window):
        self.closed = True
        self.logo_waiters.clear()
        return False

    def _refresh_remote_catalog(self, show_error):
        if not self.catalog_url or self.refresh_spinner.get_spinning():
            return
        self.refresh_button.set_sensitive(False)
        self.refresh_spinner.start()

        def worker():
            try:
                catalog = fetch_remote_catalog(self.catalog_url)
                if self.closed:
                    return
                _save_cached_catalog(self.cache_path, catalog)
                GLib.idle_add(self._finish_catalog_refresh, catalog, None, show_error)
            except (CatalogValidationError, OSError, requests.RequestException) as exc:
                GLib.idle_add(self._finish_catalog_refresh, None, str(exc), show_error)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_catalog_refresh(self, catalog, error, show_error):
        if self.closed:
            return False
        self.refresh_spinner.stop()
        self.refresh_button.set_sensitive(True)
        if catalog is not None:
            self.catalog = catalog
            self._populate_catalog()
        elif show_error:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Could not refresh the application catalog"))
            )
        return False

    def _on_application_activated(self, _row, application):
        if self.connecting:
            return
        self._show_setup(application)

    def _show_setup(self, application):
        self.selected_application = application
        self.field_entries = {}
        self.field_definitions = {
            field["id"]: field for field in application.get("setup", {}).get("fields", [])
        }

        if self.setup_page is not None:
            self.stack.remove(self.setup_page)
        self.setup_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        back_button = Gtk.Button(
            icon_name="go-previous-symbolic",
            tooltip_text=_("Back to applications"),
            css_classes=["flat"],
        )
        back_button.connect("clicked", lambda _button: self._show_catalog())
        header.pack_start(back_button)
        header.set_title_widget(
            Adw.WindowTitle(title=_catalog_text(application["name"]))
        )
        self.setup_page.append(header)

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=600)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        clamp.set_child(content)
        scroll.set_child(clamp)

        application_group = Adw.PreferencesGroup()
        application_row = Adw.ActionRow(
            title=_catalog_text(application["name"]),
            subtitle=_catalog_text(application["description"]),
        )
        application_row.add_prefix(self._create_avatar(application, size=54))
        application_group.add(application_row)
        content.append(application_group)

        setup = application.get("setup", {})
        if setup.get("instructions") or setup.get("help_url"):
            setup_group = Adw.PreferencesGroup(title=_("Setup"))
            if setup.get("instructions"):
                setup_group.add(
                    Adw.ActionRow(
                        title=_("Before connecting"),
                        subtitle=_catalog_text(setup["instructions"]),
                        icon_name="info-outline-symbolic",
                    )
                )
            if setup.get("help_url"):
                help_row = Adw.ActionRow(
                    title=_("Setup guide"),
                    subtitle=_("Open the provider's setup instructions in your browser"),
                    icon_name="internet-symbolic",
                )
                help_button = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
                help_button.connect(
                    "clicked", lambda _button, url=setup["help_url"]: open_website(url)
                )
                help_row.add_suffix(help_button)
                setup_group.add(help_row)
            content.append(setup_group)

        fields = setup.get("fields", [])
        if fields:
            fields_group = Adw.PreferencesGroup(title=_("Required information"))
            for field in fields:
                row, entry = self._build_field_row(field)
                fields_group.add(row)
                self.field_entries[field["id"]] = entry
            content.append(fields_group)

        connection_group = Adw.PreferencesGroup(title=_("Connection"))
        server = application["server"]
        if server["type"] == "http":
            self.connection_preview_row = Adw.ActionRow(
                title=_("HTTP endpoint"), subtitle=server["url"]
            )
            connection_group.add(self.connection_preview_row)
            auth_type = server.get("auth", {}).get("type", "none")
            auth_labels = {
                "none": _("No authentication"),
                "bearer": _("Token authentication"),
                "oauth": _("Browser authentication"),
            }
            connection_group.add(
                Adw.ActionRow(title=_("Authentication"), subtitle=auth_labels[auth_type])
            )
            redirect_port = server.get("auth", {}).get("redirect_port")
            if auth_type == "oauth" and redirect_port:
                callback_url = f"http://127.0.0.1:{redirect_port}/callback"
                callback_row = Adw.ActionRow(
                    title=_("OAuth callback URL"), subtitle=callback_url
                )
                copy_button = Gtk.Button(
                    icon_name="edit-copy-symbolic",
                    tooltip_text=_("Copy callback URL"),
                    css_classes=["flat"],
                    valign=Gtk.Align.CENTER,
                )
                copy_button.connect(
                    "clicked", lambda _button, text=callback_url: self._copy_text(text)
                )
                callback_row.add_suffix(copy_button)
                connection_group.add(callback_row)
        else:
            self.connection_preview_row = Adw.ActionRow(
                title=_("Command"), subtitle=self._command_preview(server)
            )
            connection_group.add(self.connection_preview_row)
            warning = Adw.ActionRow(
                title=_("Runs a local command"),
                subtitle=_(
                    "Newelle will run this command on your host when you connect. "
                    "Review it before continuing."
                ),
                icon_name="warning-outline-symbolic",
            )
            warning.add_css_class("warning")
            connection_group.add(warning)
            env_names = list(server.get("env", {}).keys())
            if env_names:
                connection_group.add(
                    Adw.ActionRow(
                        title=_("Environment variables"),
                        subtitle=", ".join(env_names),
                    )
                )
        content.append(connection_group)

        action_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=12,
        )
        self.connect_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.connect_spinner.set_visible(False)
        action_box.append(self.connect_spinner)
        auth_type = server.get("auth", {}).get("type", "none")
        button_label = _("Authenticate and Connect") if auth_type == "oauth" else _("Connect")
        self.connect_button = Gtk.Button(label=button_label, hexpand=True)
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.connect("clicked", self._connect_application)
        action_box.append(self.connect_button)
        content.append(action_box)

        can_use_stdio = (not is_flatpak()) or can_escape_sandbox()
        if server["type"] == "stdio" and not can_use_stdio:
            self.connect_button.set_sensitive(False)
            self.connect_button.set_tooltip_text(
                _("Host access is required for STDIO servers")
            )

        for entry in self.field_entries.values():
            entry.connect("changed", lambda _entry: self._update_connection_preview())

        self.setup_page.append(scroll)
        self.stack.add_named(self.setup_page, "setup")
        self.stack.set_visible_child_name("setup")

    def _build_field_row(self, field):
        row = Adw.ActionRow(
            title=_catalog_text(field["label"]),
            subtitle=_catalog_text(field.get("description")),
        )
        field_type = field.get("type", "text")
        entry = Gtk.Entry(
            text=_catalog_text(field.get("default", "")),
            placeholder_text=_catalog_text(field.get("placeholder")),
            visibility=field_type != "secret",
            hexpand=True,
            valign=Gtk.Align.CENTER,
            width_chars=28,
        )
        if field_type == "url":
            entry.set_input_purpose(Gtk.InputPurpose.URL)
        row.add_suffix(entry)

        if field_type == "secret":
            reveal = Gtk.Button(
                icon_name="view-reveal-symbolic",
                tooltip_text=_("Show or hide this value"),
                css_classes=["flat"],
                valign=Gtk.Align.CENTER,
            )
            reveal.connect(
                "clicked", lambda _button: entry.set_visibility(not entry.get_visibility())
            )
            row.add_suffix(reveal)
        elif field_type in {"file", "directory"}:
            browse = Gtk.Button(
                icon_name="document-open-symbolic" if field_type == "file" else "folder-open-symbolic",
                tooltip_text=_("Choose a file") if field_type == "file" else _("Choose a folder"),
                css_classes=["flat"],
                valign=Gtk.Align.CENTER,
            )
            browse.connect("clicked", self._choose_path, entry, field_type)
            row.add_suffix(browse)
        return row, entry

    def _choose_path(self, _button, entry, field_type):
        dialog = Gtk.FileDialog(
            title=_("Choose a file") if field_type == "file" else _("Choose a folder")
        )

        def selected(current_dialog, result):
            try:
                if field_type == "file":
                    selected_file = current_dialog.open_finish(result)
                else:
                    selected_file = current_dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if selected_file and selected_file.get_path():
                entry.set_text(selected_file.get_path())

        if field_type == "file":
            dialog.open(self, None, selected)
        else:
            dialog.select_folder(self, None, selected)

    def _collect_values(self):
        values = {}
        for field_id, entry in self.field_entries.items():
            field = self.field_definitions[field_id]
            entry.remove_css_class("error")
            value = entry.get_text().strip()
            if field.get("required", False) and not value:
                entry.add_css_class("error")
                entry.grab_focus()
                raise CatalogValidationError(
                    _("{} is required").format(_catalog_text(field["label"]))
                )
            if value and field.get("type") in {"file", "directory"}:
                value = os.path.expanduser(value)
            if value and field.get("type") == "url":
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    entry.add_css_class("error")
                    entry.grab_focus()
                    raise CatalogValidationError(
                        _("{} must be a valid URL").format(
                            _catalog_text(field["label"])
                        )
                    )
            values[field_id] = value
        return values

    def _update_connection_preview(self):
        if not self.selected_application:
            return
        values = {
            field_id: entry.get_text().strip() or f"${{{field_id}}}"
            for field_id, entry in self.field_entries.items()
        }
        server = _render_templates(self.selected_application["server"], values)
        if server["type"] == "stdio":
            self.connection_preview_row.set_subtitle(self._command_preview(server))
        else:
            self.connection_preview_row.set_subtitle(server["url"])

    @staticmethod
    def _command_preview(server):
        return shlex.join([server["command"], *server.get("args", [])])

    def _copy_text(self, text):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
            self.toast_overlay.add_toast(Adw.Toast(title=_("Copied to clipboard")))

    def _connect_application(self, _button):
        if self.connecting:
            return
        try:
            values = self._collect_values()
            server = _validate_rendered_server(
                _render_templates(self.selected_application["server"], values)
            )
        except CatalogValidationError as exc:
            self.toast_overlay.add_toast(Adw.Toast(title=str(exc)))
            return

        if self._is_server_connected(server):
            self.toast_overlay.add_toast(Adw.Toast(title=_("This application is already connected")))
            return

        if server["type"] == "stdio":
            self._confirm_stdio_connection(server)
            return
        self._start_connection(server)

    def _confirm_stdio_connection(self, server):
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.set_heading(_("Run this MCP server?"))
        dialog.set_body(
            _(
                "STDIO servers run programs on your host. Only continue if you trust "
                "the application and have reviewed the exact command below."
            )
        )
        command_label = Gtk.Label(
            label=self._command_preview(server),
            selectable=True,
            wrap=True,
            xalign=0,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        command_label.add_css_class("monospace")
        command_label.add_css_class("card")
        dialog.set_extra_child(command_label)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("run", _("Run and Connect"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("run")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)

        def responded(current_dialog, response_id):
            current_dialog.destroy()
            if response_id == "run":
                self._start_connection(server)

        dialog.connect("response", responded)
        dialog.present()

    def _start_connection(self, server):
        self.connecting = True
        self.connect_button.set_sensitive(False)
        self.connect_spinner.set_visible(True)
        self.connect_spinner.start()
        original_label = self.connect_button.get_label()
        auth = server.get("auth", {"type": "none"})
        application_name = _catalog_text(self.selected_application["name"])
        application_id = self.selected_application["id"]
        if auth.get("type") == "oauth":
            self.connect_button.set_label(_("Waiting for browser authentication…"))
        else:
            self.connect_button.set_label(_("Connecting…"))

        def worker():
            oauth_completed = False
            try:
                handler = self.controller.get_mcp_integration()
                if handler is None:
                    raise RuntimeError(_("The MCP integration is not available"))

                if server["type"] == "http" and auth.get("type") == "oauth":
                    from ..integrations.mcp_oauth import run_oauth_flow

                    success, error = run_oauth_flow(
                        server["url"],
                        self.controller.config_dir,
                        client_id=auth.get("client_id") or None,
                        client_secret=auth.get("client_secret") or None,
                        redirect_port=auth.get("redirect_port"),
                        scopes=auth.get("scopes"),
                        authorization_params=auth.get("authorization_params"),
                        token_endpoint_auth_method=auth.get("token_endpoint_auth_method"),
                    )
                    if not success:
                        raise RuntimeError(error or _("Authentication failed"))
                    oauth_completed = True

                if server["type"] == "stdio":
                    prepared = handler.prepare_mcp_server(
                        title=server.get("title") or application_name,
                        server_type="stdio",
                        command=os.path.expanduser(server["command"]),
                        args=server.get("args", []),
                        env=server.get("env") or None,
                    )
                else:
                    bearer_token = auth.get("token") if auth.get("type") == "bearer" else None
                    prepared = handler.prepare_mcp_server(
                        url=server["url"],
                        title=server.get("title") or application_name,
                        bearer_token=bearer_token,
                        custom_headers=server.get("headers") or None,
                        server_type="http",
                        oauth_mode=auth.get("type") == "oauth",
                    )
                if prepared is None:
                    raise RuntimeError(_("The MCP server could not be added"))
                prepared[0]["catalog_id"] = application_id
                if self.closed:
                    if oauth_completed:
                        from ..integrations.mcp_oauth import clear_oauth_credentials

                        clear_oauth_credentials(server["url"], self.controller.config_dir)
                    return
                GLib.idle_add(
                    self._finish_connection,
                    handler,
                    prepared,
                    server,
                    oauth_completed,
                    None,
                    original_label,
                    application_name,
                )
            except Exception as exc:
                if oauth_completed:
                    from ..integrations.mcp_oauth import clear_oauth_credentials

                    clear_oauth_credentials(server["url"], self.controller.config_dir)
                if not self.closed:
                    GLib.idle_add(
                        self._finish_connection,
                        None,
                        None,
                        server,
                        False,
                        str(exc),
                        original_label,
                        application_name,
                    )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_connection(
        self,
        handler,
        prepared,
        server,
        oauth_completed,
        error,
        original_label,
        application_name,
    ):
        if self.closed:
            if oauth_completed:
                from ..integrations.mcp_oauth import clear_oauth_credentials

                clear_oauth_credentials(server["url"], self.controller.config_dir)
            return False

        if error is None:
            try:
                if self._is_server_connected(server):
                    raise RuntimeError(_("This application is already connected"))
                if not handler.commit_mcp_server(*prepared):
                    raise RuntimeError(_("The MCP server could not be added"))
            except Exception as exc:
                error = str(exc)
                if oauth_completed and not self._is_server_connected(server):
                    from ..integrations.mcp_oauth import clear_oauth_credentials

                    clear_oauth_credentials(server["url"], self.controller.config_dir)

        self.connecting = False
        self.connect_spinner.stop()
        self.connect_spinner.set_visible(False)
        if error is not None:
            self.connect_button.set_sensitive(True)
            self.connect_button.set_label(original_label)
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_("Could not connect {}: {}").format(
                        application_name, error
                    )
                )
            )
            return False

        self.controller.settings.set_string("mcp-servers", json.dumps(handler.mcp_servers))
        self.controller.newelle_settings.mcp_servers_dict = list(handler.mcp_servers)
        if self.on_connected is not None:
            self.on_connected()
        for field_id, field in self.field_definitions.items():
            if field.get("type") == "secret":
                self.field_entries[field_id].set_text("")
        self.connect_button.set_label(_("Connected"))
        self.toast_overlay.add_toast(
            Adw.Toast(
                title=_("{} connected").format(application_name)
            )
        )
        return False

    def _show_catalog(self):
        if self.connecting:
            return
        self._populate_catalog()
        self.stack.set_visible_child_name("catalog")

    def _is_application_connected(self, application):
        handler = self.controller.get_mcp_integration()
        if handler is not None and any(
            isinstance(existing, dict)
            and existing.get("catalog_id") == application["id"]
            for existing in handler.mcp_servers
        ):
            return True
        server = application["server"]
        if server["type"] == "http":
            if PLACEHOLDER_RE.search(server["url"]):
                return False
        else:
            identifier_parts = [server["command"], *server.get("args", [])]
            if any(PLACEHOLDER_RE.search(part) for part in identifier_parts):
                return False
        return self._is_server_connected(server)

    def _is_server_connected(self, server):
        handler = self.controller.get_mcp_integration()
        if handler is None:
            return False
        if server["type"] == "stdio":
            identifier = handler._get_server_identifier(
                {
                    "type": "stdio",
                    "command": os.path.expanduser(server["command"]),
                    "args": server.get("args", []),
                }
            )
        else:
            identifier = server["url"]
        return any(
            handler._get_server_identifier(handler._get_server_info(existing)) == identifier
            for existing in handler.mcp_servers
        )
