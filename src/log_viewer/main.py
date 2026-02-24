"""Log Viewer - GTK4 journalctl frontend."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Pango
import subprocess
import threading
import gettext
from datetime import datetime
from log_viewer.accessibility import AccessibilityManager

_ = gettext.gettext
APP_ID = "io.github.yeager.LogViewer"

PRIORITY_COLORS = {
    "0": "#cc0000",  # emerg
    "1": "#cc0000",  # alert
    "2": "#cc0000",  # crit
    "3": "#e01b24",  # err
    "4": "#e66100",  # warning
    "5": "#1c71d8",  # notice
    "6": "#2ec27e",  # info
    "7": "#77767b",  # debug
}

PRIORITY_NAMES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]



def _wlc_settings_path():
    import os
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    d = os.path.join(xdg, "log-viewer")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "welcome.json")

def _load_wlc_settings():
    import os, json
    p = _wlc_settings_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"welcome_shown": False}

def _save_wlc_settings(s):
    import json
    with open(_wlc_settings_path(), "w") as f:
        json.dump(s, f, indent=2)

class LogViewerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs, title=_("Log Viewer"), default_width=1100, default_height=700)
        self._follow_proc = None
        self._follow_running = False

        header = Adw.HeaderBar()
        self.theme_btn = Gtk.Button(icon_name="weather-clear-night-symbolic", tooltip_text=_("Toggle theme"))
        self.theme_btn.connect("clicked", self._toggle_theme)
        header.pack_end(self.theme_btn)
        about_btn = Gtk.Button(icon_name="help-about-symbolic")
        about_btn.connect("clicked", self._show_about)
        header.pack_end(about_btn)

        export_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Export logs"))
        export_btn.connect("clicked", self._export_logs)
        header.pack_end(export_btn)

        # Filters bar
        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_start=12, margin_end=12, margin_top=8)

        filters.append(Gtk.Label(label=_("Unit:")))
        self.unit_entry = Gtk.Entry(placeholder_text=_("e.g. sshd, NetworkManager"), hexpand=True)
        filters.append(self.unit_entry)

        filters.append(Gtk.Label(label=_("Priority:")))
        prio_list = Gtk.StringList.new([_("All")] + PRIORITY_NAMES)
        self.prio_combo = Gtk.DropDown(model=prio_list)
        filters.append(self.prio_combo)

        filters.append(Gtk.Label(label=_("Since:")))
        self.since_entry = Gtk.Entry(placeholder_text="1h ago / 2024-01-01", width_chars=16)
        filters.append(self.since_entry)

        search_btn = Gtk.Button(label=_("Load"), css_classes=["suggested-action"])
        search_btn.connect("clicked", self._load_logs)
        filters.append(search_btn)

        self.follow_btn = Gtk.ToggleButton(label=_("Follow"), css_classes=["destructive-action"])
        self.follow_btn.connect("toggled", self._toggle_follow)
        filters.append(self.follow_btn)

        # Search
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                             margin_start=12, margin_end=12, margin_top=4)
        search_bar.append(Gtk.Label(label=_("Search:")))
        self.search_entry = Gtk.Entry(placeholder_text=_("Filter text..."), hexpand=True)
        self.search_entry.connect("changed", self._filter_view)
        search_bar.append(self.search_entry)

        # Log view
        sw = Gtk.ScrolledWindow(vexpand=True, margin_start=12, margin_end=12, margin_top=8, margin_bottom=4)
        self.log_store = Gtk.ListStore(str, str, str)  # priority, timestamp, message
        self.log_filter = self.log_store.filter_new()
        self.log_filter.set_visible_func(self._filter_func)
        self.tree = Gtk.TreeView(model=self.log_filter, headers_visible=True)
        self.tree.set_enable_search(True)

        for i, title in enumerate([_("Priority"), _("Timestamp"), _("Message")]):
            renderer = Gtk.CellRendererText()
            if i == 0:
                renderer.set_property("font", "monospace 9")
            elif i == 2:
                renderer.set_property("font", "monospace 9")
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_resizable(True)
            if i == 2:
                col.set_expand(True)
            self.tree.append_column(col)

        sw.set_child(self.tree)

        self.statusbar = Gtk.Label(label="", xalign=0, css_classes=["dim-label"], margin_start=12, margin_bottom=4)
        self._all_lines = []

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(filters)
        content.append(search_bar)
        content.append(sw)
        content.append(self.statusbar)
        self.set_content(content)

        self._update_status()
        GLib.timeout_add_seconds(1, self._update_status)

    def _build_cmd(self, follow=False):
        cmd = ["journalctl", "--no-pager", "-o", "short-iso"]
        unit = self.unit_entry.get_text().strip()
        if unit:
            cmd += ["-u", unit]
        prio_idx = self.prio_combo.get_selected()
        if prio_idx > 0:
            cmd += ["-p", str(prio_idx - 1)]
        since = self.since_entry.get_text().strip()
        if since:
            cmd += ["--since", since]
        if follow:
            cmd += ["-f"]
        else:
            cmd += ["-n", "1000"]
        return cmd

    def _load_logs(self, _btn=None):
        self.log_store.clear()
        self._all_lines.clear()
        cmd = self._build_cmd()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
            for line in lines:
                self._add_line(line)
        except Exception as e:
            self.log_store.append(["3", "", str(e)])
        self._update_status()

    def _add_line(self, line):
        # Try to parse priority from line - journalctl short-iso format
        parts = line.split(" ", 3)
        ts = parts[0] if parts else ""
        msg = parts[-1] if len(parts) > 1 else line
        prio = "6"  # default info
        for kw, p in [("error", "3"), ("err", "3"), ("warning", "4"), ("warn", "4"),
                       ("crit", "2"), ("alert", "1"), ("emerg", "0"), ("debug", "7"), ("notice", "5")]:
            if kw in line.lower():
                prio = p
                break
        self.log_store.append([prio, ts, msg])
        self._all_lines.append(line)

    def _toggle_follow(self, btn):
        if btn.get_active():
            self._follow_running = True
            t = threading.Thread(target=self._follow_thread, daemon=True)
            t.start()
        else:
            self._follow_running = False
            if self._follow_proc:
                try:
                    self._follow_proc.terminate()
                except Exception:
                    pass

    def _follow_thread(self):
        cmd = self._build_cmd(follow=True)
        try:
            self._follow_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in self._follow_proc.stdout:
                if not self._follow_running:
                    break
                line = line.strip()
                if line:
                    GLib.idle_add(self._add_line, line)
        except Exception:
            pass

    def _filter_func(self, model, iter, _data=None):
        search = self.search_entry.get_text().lower()
        if not search:
            return True
        msg = model[iter][2] or ""
        return search in msg.lower()

    def _filter_view(self, *_args):
        self.log_filter.refilter()

    def _export_logs(self, _btn):
        dialog = Gtk.FileChooserNative(
            title=_("Export Logs"), transient_for=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.set_current_name("logs.txt")
        dialog.connect("response", self._on_export_response)
        dialog.show()

    def _on_export_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            with open(path, 'w') as f:
                f.write("\n".join(self._all_lines))

    def _update_status(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = len(self.log_store)
        self.statusbar.set_label(f"  {count} entries | {now}")
        return True

    def _toggle_theme(self, _btn):
        mgr = Adw.StyleManager.get_default()
        if mgr.get_dark():
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def _show_about(self, _btn):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="Log Viewer",
            application_icon="utilities-terminal",
            version="0.1.0",
            developer_name="Daniel Nylander",
            developers=["Daniel Nylander"],
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/yeager/log-viewer",
            issue_url="https://github.com/yeager/log-viewer/issues",
            translator_credits=_("translator-credits"),
            comments=_("GTK4 journalctl frontend"),
        )
        about.add_link(_("Translations"), "https://www.transifex.com/danielnylander/log-viewer")
        about.present(self)


class LogViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        GLib.set_application_name(_("Log Viewer"))

    def do_activate(self):
        win = self.props.active_window or LogViewerWindow(application=self)
        win.present()
        # Welcome dialog
        self._wlc_settings = _load_wlc_settings()
        if not self._wlc_settings.get("welcome_shown"):
            self._show_welcome(self.props.active_window or self)


    def do_startup(self):
        Adw.Application.do_startup(self)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])


def main():
    app = LogViewerApp()
    app.run()


if __name__ == "__main__":
    main()

    def _show_welcome(self, win):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)
        page = Adw.StatusPage()
        page.set_icon_name("text-x-generic-symbolic")
        page.set_title(_("Welcome to Log Viewer"))
        page.set_description(_("View and analyze log files.\n\n✓ Real-time log tailing\n✓ Search and filter\n✓ Syntax highlighting"))
        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(12)
        btn.connect("clicked", self._on_welcome_close, dialog)
        page.set_child(btn)
        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)
        box.set_content(page)
        dialog.set_child(box)
        dialog.present(win)

    def _on_welcome_close(self, btn, dialog):
        self._wlc_settings["welcome_shown"] = True
        _save_wlc_settings(self._wlc_settings)
        dialog.close()



# --- Session restore ---
import json as _json
import os as _os

def _save_session(window, app_name):
    config_dir = _os.path.join(_os.path.expanduser('~'), '.config', app_name)
    _os.makedirs(config_dir, exist_ok=True)
    state = {'width': window.get_width(), 'height': window.get_height(),
             'maximized': window.is_maximized()}
    try:
        with open(_os.path.join(config_dir, 'session.json'), 'w') as f:
            _json.dump(state, f)
    except OSError:
        pass

def _restore_session(window, app_name):
    path = _os.path.join(_os.path.expanduser('~'), '.config', app_name, 'session.json')
    try:
        with open(path) as f:
            state = _json.load(f)
        window.set_default_size(state.get('width', 800), state.get('height', 600))
        if state.get('maximized'):
            window.maximize()
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        pass


# --- Fullscreen toggle (F11) ---
def _setup_fullscreen(window, app):
    """Add F11 fullscreen toggle."""
    from gi.repository import Gio
    if not app.lookup_action('toggle-fullscreen'):
        action = Gio.SimpleAction.new('toggle-fullscreen', None)
        action.connect('activate', lambda a, p: (
            window.unfullscreen() if window.is_fullscreen() else window.fullscreen()
        ))
        app.add_action(action)
        app.set_accels_for_action('app.toggle-fullscreen', ['F11'])


# --- Plugin system ---
import importlib.util
import os as _pos

def _load_plugins(app_name):
    """Load plugins from ~/.config/<app>/plugins/."""
    plugin_dir = _pos.path.join(_pos.path.expanduser('~'), '.config', app_name, 'plugins')
    plugins = []
    if not _pos.path.isdir(plugin_dir):
        return plugins
    for fname in sorted(_pos.listdir(plugin_dir)):
        if fname.endswith('.py') and not fname.startswith('_'):
            path = _pos.path.join(plugin_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(fname[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                plugins.append(mod)
            except Exception as e:
                print(f"Plugin {fname}: {e}")
    return plugins
