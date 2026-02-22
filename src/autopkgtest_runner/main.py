"""Autopkgtest Runner — GTK4 frontend for running Debian autopkgtests."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango

import gettext
import locale
import os
import sys
import json
import datetime
import threading
import subprocess
import re
from autopkgtest_runner.accessibility import AccessibilityManager

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "po")
if not os.path.isdir(LOCALE_DIR):
    LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain("autopkgtest-runner", LOCALE_DIR)
gettext.bindtextdomain("autopkgtest-runner", LOCALE_DIR)
gettext.textdomain("autopkgtest-runner")
_ = gettext.gettext

APP_ID = "se.danielnylander.autopkgtest.runner"
SETTINGS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "autopkgtest-runner"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)





class AutopkgtestRunnerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Autopkgtest Runner"), default_width=1000, default_height=700)
        self.settings = _load_settings()
        
        self._test_results = []
        self._running = False

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=_("Autopkgtest Runner"), subtitle="")
        headerbar.set_title_widget(title_widget)
        self._title_widget = title_widget

        
        open_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Open package directory"))
        open_btn.connect("clicked", self._on_open_pkg)
        headerbar.pack_start(open_btn)
        
        self._run_btn = Gtk.Button(label=_("Run Tests"))
        self._run_btn.add_css_class("suggested-action")
        self._run_btn.set_sensitive(False)
        self._run_btn.connect("clicked", self._on_run)
        headerbar.pack_end(self._run_btn)

        # Menu
        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Copy Debug Info"), "app.copy-debug")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Autopkgtest Runner"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        headerbar.pack_end(menu_btn)

        main_box.append(headerbar)

        
        # Paned: test list + output
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_vexpand(True)
        
        # Top: test list
        top_scroll = Gtk.ScrolledWindow(min_content_height=200)
        self._test_list = Gtk.ListBox()
        self._test_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._test_list.add_css_class("boxed-list")
        self._test_list.set_margin_start(12)
        self._test_list.set_margin_end(12)
        self._test_list.set_margin_top(8)
        top_scroll.set_child(self._test_list)
        paned.set_start_child(top_scroll)
        
        # Bottom: output log
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        out_label = Gtk.Label(label=_("Test Output"), xalign=0)
        out_label.add_css_class("heading")
        out_label.set_margin_start(12)
        out_label.set_margin_top(4)
        bottom_box.append(out_label)
        
        out_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._output_view = Gtk.TextView(editable=False, monospace=True)
        self._output_view.set_top_margin(8)
        self._output_view.set_left_margin(8)
        out_scroll.set_child(self._output_view)
        bottom_box.append(out_scroll)
        paned.set_end_child(bottom_box)
        paned.set_position(300)
        
        main_box.append(paned)
        
        self._pkg_dir = None

        # Status bar
        self._status = Gtk.Label(label=_("Ready"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("dim-label")
        main_box.append(self._status)

        self.set_content(main_box)

        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("system-run-symbolic")
        page.set_title(_("Welcome to Autopkgtest Runner"))
        page.set_description(_("Run Debian autopkgtests easily.\n\n"
            "✓ Run autopkgtest on local packages\n"
            "✓ Visual pass/fail results\n"
            "✓ Live test output streaming\n"
            "✓ Test history and comparison\n"
            "✓ Export test reports"))

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
        dialog.present(self)

    def _on_welcome_close(self, btn, dialog):
        self.settings["welcome_shown"] = True
        _save_settings(self.settings)
        dialog.close()

    
    def _on_open_pkg(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select package directory"))
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            f = dialog.select_folder_finish(result)
            self._pkg_dir = f.get_path()
            self._run_btn.set_sensitive(True)
            self._title_widget.set_subtitle(os.path.basename(self._pkg_dir))
            # Check for debian/tests
            test_dir = os.path.join(self._pkg_dir, "debian", "tests")
            if os.path.isdir(test_dir):
                control = os.path.join(test_dir, "control")
                if os.path.exists(control):
                    with open(control) as fh:
                        self._status.set_text(_("Found test control: %s") % control)
                else:
                    self._status.set_text(_("No debian/tests/control found"))
            else:
                self._status.set_text(_("No debian/tests directory"))
        except:
            pass

    def _on_run(self, btn):
        if not self._pkg_dir:
            return
        if self._running:
            self._running = False
            self._run_btn.set_label(_("Run Tests"))
            return
        
        self._running = True
        self._run_btn.set_label(_("Stop"))
        self._run_btn.remove_css_class("suggested-action")
        self._run_btn.add_css_class("destructive-action")
        self._output_view.get_buffer().set_text("")
        threading.Thread(target=self._run_tests, daemon=True).start()

    def _run_tests(self):
        try:
            proc = subprocess.Popen(
                ["autopkgtest", self._pkg_dir, "--", "null"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True
            )
            for line in iter(proc.stdout.readline, ""):
                if not self._running:
                    proc.terminate()
                    break
                GLib.idle_add(self._append_output, line)
            proc.wait()
            GLib.idle_add(self._test_done, proc.returncode)
        except FileNotFoundError:
            GLib.idle_add(self._append_output, _("autopkgtest not installed. Install with: sudo apt install autopkgtest\n"))
            GLib.idle_add(self._test_done, -1)
        except Exception as e:
            GLib.idle_add(self._append_output, str(e) + "\n")
            GLib.idle_add(self._test_done, -1)

    def _append_output(self, text):
        buf = self._output_view.get_buffer()
        buf.insert(buf.get_end_iter(), text)

    def _test_done(self, returncode):
        self._running = False
        self._run_btn.set_label(_("Run Tests"))
        self._run_btn.remove_css_class("destructive-action")
        self._run_btn.add_css_class("suggested-action")
        
        if returncode == 0:
            icon = "✅"
            status_text = _("All tests passed")
        elif returncode == -1:
            icon = "⚠️"
            status_text = _("Could not run tests")
        else:
            icon = "❌"
            status_text = _("Tests failed (exit code %(code)d)") % {"code": returncode}
        
        row = Adw.ActionRow()
        row.set_title(f"{icon} {status_text}")
        row.set_subtitle(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._test_list.prepend(row)
        
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._status.set_text(_("%(time)s — %(status)s") % {"time": ts, "status": status_text})


class AutopkgtestRunnerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

        for name, callback in [
            ("settings", self._on_settings),
            ("copy-debug", self._on_copy_debug),
            ("shortcuts", self._on_shortcuts),
            ("about", self._on_about),
            ("quit", self._on_quit),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.shortcuts", ["<Ctrl>slash"])

    def do_activate(self):
        if not self.window:
            self.window = AutopkgtestRunnerWindow(self)
        self.window.present()

    def _on_settings(self, *_args):
        if not self.window:
            return
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))
        page = Adw.PreferencesPage()
        
        group = Adw.PreferencesGroup(title=_("Test Runner"))
        row = Adw.ComboRow(title=_("Virtualization"))
        row.set_model(Gtk.StringList.new(["null", "schroot", "lxc", "qemu"]))
        group.add(row)
        page.add(group)
        dialog.add(page)
        dialog.present(self.window)

    def _on_copy_debug(self, *_args):
        if not self.window:
            return
        from . import __version__
        info = (
            f"Autopkgtest Runner {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_args):
        if self.window:
            dialog = Gtk.ShortcutsWindow(transient_for=self.window)
            section = Gtk.ShortcutsSection(visible=True)
            group = Gtk.ShortcutsGroup(title=_("General"), visible=True)
            for accel, title in [
                ("<Ctrl>q", _("Quit")),
                ("<Ctrl>slash", _("Keyboard shortcuts")),
            ]:
                group.append(Gtk.ShortcutsShortcut(accelerator=accel, title=title, visible=True))
            section.append(group)
            dialog.append(section)
            dialog.present()

    def _on_about(self, *_args):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("Autopkgtest Runner"),
            application_icon="system-run-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/autopkgtest-runner",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/autopkgtest-runner/issues",
            comments=_("Run autopkgtests locally without sbuild. Visual test output with pass/fail indicators."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_args):
        self.quit()


def main():
    app = AutopkgtestRunnerApp()
    app.run(sys.argv)


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
