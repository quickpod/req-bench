#!/usr/bin/env python3
r"""ReqBench -- an Aura (QuickOpen design system) GUI on top of the ``reqbench`` API.

A single Aura window: a sidebar (Request, Collections, History, Code-gen,
About) and a swappable content area.  The Request builder composes a
method/URL/headers/params/body/auth request, sends it on a background thread
(so the UI never freezes), and shows the parsed response -- pretty JSON,
headers and timing -- inline.  Failures surface as the :class:`ReqBenchError`
message in the Aura status bar, never as a raw traceback.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``reqbench/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a note, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.
  * Requests run on background threads; results are marshalled back with
    ``self.after`` and errors land in the status bar.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import json
import os
import sys
import threading

# tkinter/customtkinter are imported lazily inside build_app()/main() so that
# merely importing this module (during packaging or on a headless CI box)
# never fails.

APP_NAME = "ReqBench"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "ReqBench — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#17914b"      # publish/specs/req-bench.json "accent": [23, 145, 75]
MONO = "Consolas"       # falls back to a fixed font off Windows


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def human_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    import customtkinter as ctk

    from . import aura, guiconfig
    from . import collections as col
    from . import history as hist
    from .codegen import generate, LANGUAGES
    from .errors import ReqBenchError
    from .http import send
    from .model import Request, METHODS

    BODY_LABELS = {"none": "None", "json": "JSON", "form": "Form", "raw": "Raw"}
    AUTH_LABELS = {"none": "None", "basic": "Basic", "bearer": "Bearer"}

    def _parse_kv_lines(text, sep):
        """Parse 'key<sep>value' lines into an ordered dict; blanks ignored."""
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or sep not in line:
                continue
            k, v = line.split(sep, 1)
            k = k.strip()
            if k:
                out[k] = v.strip()
        return out

    def _kv_to_lines(mapping, sep):
        return "\n".join(f"{k}{sep} {v}" for k, v in (mapping or {}).items())

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("req-bench.png"), version=APP_VERSION,
                tagline="offline REST & GraphQL",
                on_theme_change=guiconfig.set_theme,
                size=(1120, 720), min_size=(940, 600))

            self._busy = False
            self._cancelled = False
            self._img_refs_gui = []

            self._set_icon()
            self._build_menu()
            self.add_section("request", "Request", "⇄", self._view_request)
            self.add_section("collections", "Collections", "⛁",
                             self._view_collections)
            self.add_section("history", "History", "↻", self._view_history)
            self.add_section("codegen", "Code-gen", "⊞", self._view_codegen)
            self.add_section("about", "About", "ℹ", self._view_about)
            self.show("request")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self.destroy)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("req-bench.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("req-bench.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (native menus stay; theme also lives in the sidebar toggle)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Send request", accelerator="Ctrl+Enter",
                              command=self._send_current)
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-Return>", lambda e: self._send_current())

        # ---- section switching (reload data views on every visit)
        def show(self, sid):
            super().show(sid)
            if sid == "collections":
                self._reload_collections()
            elif sid == "history":
                self._reload_history()

        # ---- small helpers
        @staticmethod
        def _fill(entry, text):
            entry.delete(0, "end")
            if text:
                entry.insert(0, text)

        def _mono_text(self, parent, **kw):
            """A themed raw tk.Text (registered with the Aura tracker)."""
            w = tk.Text(parent, font=(MONO, 10), **kw)
            aura.track(w, "text")
            return w

        # =================================================================
        # Request builder section
        # =================================================================
        def _view_request(self, root):
            # URL row
            urlrow = ctk.CTkFrame(root, fg_color="transparent")
            urlrow.pack(fill="x")
            self.method_var = tk.StringVar(value="GET")
            aura.AuraCombo(urlrow, variable=self.method_var,
                           values=list(METHODS), state="readonly",
                           width=110).pack(side="left")
            # no textvariable: CTkEntry placeholders only work without one
            self.url_entry = aura.AuraEntry(
                urlrow, placeholder="https://api.example.com/path — "
                                    "{{variables}} come from the active "
                                    "environment")
            self.url_entry.pack(side="left", fill="x", expand=True, padx=8)
            self.url_entry.bind("<Return>", lambda e: self._send_current())
            self.send_btn = aura.AuraButton(urlrow, "Send", kind="primary",
                                            command=self._send_current)
            self.send_btn.pack(side="left")
            self.cancel_btn = aura.AuraButton(urlrow, "Cancel",
                                              kind="secondary",
                                              command=self._cancel_send)
            self.cancel_btn.pack(side="left", padx=(8, 0))
            self.cancel_btn.state(["disabled"])

            # request tabs (ttk.Notebook stays — Aura restyles it)
            nb = ttk.Notebook(root)
            nb.pack(fill="x", pady=(12, 8))

            params_tab = ttk.Frame(nb, padding=8)
            nb.add(params_tab, text="Params")
            ttk.Label(params_tab, style="Muted.TLabel",
                      text="One 'key = value' per line.").pack(anchor="w")
            self.params_txt = self._mono_text(params_tab, height=4, wrap="none")
            self.params_txt.pack(fill="x", pady=(4, 0))

            headers_tab = ttk.Frame(nb, padding=8)
            nb.add(headers_tab, text="Headers")
            ttk.Label(headers_tab, style="Muted.TLabel",
                      text="One 'Name: value' per line.").pack(anchor="w")
            self.headers_txt = self._mono_text(headers_tab, height=4,
                                               wrap="none")
            self.headers_txt.pack(fill="x", pady=(4, 0))

            body_tab = ttk.Frame(nb, padding=8)
            nb.add(body_tab, text="Body")
            self.body_type = tk.StringVar(value="none")
            brow = ctk.CTkFrame(body_tab, fg_color="transparent")
            brow.pack(fill="x", anchor="w")
            self.body_seg = aura.SegmentedControl(
                brow, values=list(BODY_LABELS.values()),
                command=lambda v: self.body_type.set(v.lower()))
            self.body_seg.set(BODY_LABELS["none"])
            self.body_seg.pack(side="left")
            ttk.Label(body_tab, style="Muted.TLabel",
                      text="JSON: an object · Form: 'key = value' lines · "
                           "Raw: verbatim").pack(anchor="w", pady=(6, 0))
            self.body_txt = self._mono_text(body_tab, height=6, wrap="word")
            self.body_txt.pack(fill="both", expand=True, pady=(4, 0))

            auth_tab = ttk.Frame(nb, padding=8)
            nb.add(auth_tab, text="Auth")
            self.auth_type = tk.StringVar(value="none")
            arow = ctk.CTkFrame(auth_tab, fg_color="transparent")
            arow.pack(fill="x", anchor="w")
            self.auth_seg = aura.SegmentedControl(
                arow, values=list(AUTH_LABELS.values()),
                command=lambda v: self.auth_type.set(v.lower()))
            self.auth_seg.set(AUTH_LABELS["none"])
            self.auth_seg.pack(side="left")
            grid = ctk.CTkFrame(auth_tab, fg_color="transparent")
            grid.pack(fill="x", pady=(8, 0), anchor="w")
            aura.Caption(grid, "User / Token").grid(
                row=0, column=0, sticky="w", padx=(0, 8))
            self.auth_user = tk.StringVar()
            aura.AuraEntry(grid, textvariable=self.auth_user, width=280).grid(
                row=0, column=1, sticky="w")
            aura.Caption(grid, "Password").grid(
                row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            self.auth_pass = tk.StringVar()
            aura.AuraEntry(grid, textvariable=self.auth_pass, show="•",
                           width=280).grid(row=1, column=1, sticky="w",
                                           pady=(6, 0))

            # save-to-collection row
            saverow = ctk.CTkFrame(root, fg_color="transparent")
            saverow.pack(fill="x", pady=(0, 10))
            aura.AuraButton(saverow, "Save to collection…", kind="secondary",
                            command=self._save_current_request).pack(
                side="left")
            aura.AuraButton(saverow, "Clear", kind="ghost",
                            command=self._clear_builder).pack(
                side="left", padx=(8, 0))

            # response viewer
            aura.SectionLabel(root, "Response").pack(anchor="w")
            self.resp_status = ctk.CTkLabel(
                root, text="No response yet.", font=aura.font(),
                text_color=aura._pair("muted"), anchor="w")
            self.resp_status.pack(anchor="w", pady=(2, 4))
            rnb = ttk.Notebook(root)
            rnb.pack(fill="both", expand=True)
            rb = ttk.Frame(rnb, padding=4)
            rnb.add(rb, text="Body")
            self.resp_body = self._mono_text(rb, wrap="none")
            self.resp_body.pack(fill="both", expand=True)
            rh = ttk.Frame(rnb, padding=4)
            rnb.add(rh, text="Headers")
            self.resp_headers = self._mono_text(rh, wrap="none")
            self.resp_headers.pack(fill="both", expand=True)

        def _read_builder(self):
            """Read the builder widgets into a Request (raising on bad input)."""
            body_type = self.body_type.get()
            body = None
            raw = self.body_txt.get("1.0", "end").strip()
            if body_type == "json":
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError as exc:
                        raise ReqBenchError(f"Body is not valid JSON: {exc}")
            elif body_type == "form":
                body = _parse_kv_lines(raw, "=")
            elif body_type == "raw":
                body = raw
            auth_type = self.auth_type.get()
            auth = None
            if auth_type == "basic":
                auth = [self.auth_user.get(), self.auth_pass.get()]
            elif auth_type == "bearer":
                auth = self.auth_user.get().strip()
            return Request(
                method=self.method_var.get(),
                url=self.url_entry.get().strip(),
                headers=_parse_kv_lines(self.headers_txt.get("1.0", "end"), ":"),
                params=_parse_kv_lines(self.params_txt.get("1.0", "end"), "="),
                body_type=body_type,
                body=body,
                auth_type=auth_type,
                auth=auth,
            )

        def _load_into_builder(self, req):
            req = Request.from_dict(req)
            self.method_var.set(req.method)
            self._fill(self.url_entry, req.url)
            self.headers_txt.delete("1.0", "end")
            self.headers_txt.insert("1.0", _kv_to_lines(req.headers, ":"))
            self.params_txt.delete("1.0", "end")
            self.params_txt.insert("1.0", _kv_to_lines(req.params, "="))
            self.body_type.set(req.body_type)
            self.body_seg.set(BODY_LABELS.get(req.body_type, "None"))
            self.body_txt.delete("1.0", "end")
            if req.body_type == "json" and req.body is not None:
                self.body_txt.insert("1.0", json.dumps(req.body, indent=2))
            elif req.body_type == "form" and isinstance(req.body, dict):
                self.body_txt.insert("1.0", _kv_to_lines(req.body, "="))
            elif req.body is not None:
                self.body_txt.insert("1.0", str(req.body))
            self.auth_type.set(req.auth_type)
            self.auth_seg.set(AUTH_LABELS.get(req.auth_type, "None"))
            if req.auth_type == "basic" and isinstance(req.auth, (list, tuple)):
                self.auth_user.set(req.auth[0] if req.auth else "")
                self.auth_pass.set(req.auth[1] if len(req.auth) > 1 else "")
            elif req.auth_type == "bearer":
                self.auth_user.set(req.auth or "")
                self.auth_pass.set("")
            else:
                self.auth_user.set("")
                self.auth_pass.set("")

        def _clear_builder(self):
            self._load_into_builder(Request(method="GET", url=""))
            self.resp_status.configure(text="No response yet.",
                                       text_color=aura._pair("muted"))
            self.resp_body.delete("1.0", "end")
            self.resp_headers.delete("1.0", "end")

        def _send_current(self):
            if self.active_section != "request":
                self.show("request")
            try:
                req = col.apply_environment(self._read_builder())
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            if not req.url:
                self.set_error("Enter a URL first.")
                return

            def show(resp):
                self._show_response(resp)
                try:
                    hist.append(resp.request or req, resp)
                except ReqBenchError:
                    pass
            self._bg(lambda: send(req), show, button=self.send_btn)

        def _cancel_send(self):
            if self._busy:
                self._cancelled = True
                self.set_error("Cancelled — ignoring the in-flight response.")

        def _show_response(self, resp):
            if resp.ok:
                color = aura._pair("ok")
            elif resp.status >= 400:
                color = aura._pair("danger")
            else:
                color = aura._pair("muted")
            self.resp_status.configure(
                text=f"{resp.status} {resp.reason}   ·   {resp.elapsed_ms:.0f} ms"
                     f"   ·   {human_size(resp.size)}",
                text_color=color)
            self.resp_body.delete("1.0", "end")
            self.resp_body.insert("1.0", resp.pretty_body())
            self.resp_headers.delete("1.0", "end")
            self.resp_headers.insert(
                "1.0", "\n".join(f"{k}: {v}" for k, v in resp.headers.items()))
            self.set_success(f"Done — {resp.status} in "
                             f"{resp.elapsed_ms:.0f} ms.")

        # =================================================================
        # Collections section
        # =================================================================
        def _view_collections(self, root):
            envrow = ctk.CTkFrame(root, fg_color="transparent")
            envrow.pack(fill="x", pady=(0, 12))
            ctk.CTkLabel(envrow, text="Active environment:",
                         font=aura.font(),
                         text_color=aura._pair("muted")).pack(side="left")
            self.env_var = tk.StringVar()
            self.env_combo = aura.AuraCombo(
                envrow, variable=self.env_var, values=["(none)"],
                state="readonly", width=220,
                command=lambda _v: self._on_env_change())
            self.env_combo.pack(side="left", padx=8)
            aura.AuraButton(envrow, "New env…", kind="secondary",
                            command=self._new_environment).pack(side="left")
            aura.AuraButton(envrow, "Set variable…", kind="secondary",
                            command=self._set_env_var).pack(
                side="left", padx=(8, 0))

            mid = ctk.CTkFrame(root, fg_color="transparent")
            mid.pack(fill="both", expand=True)
            self.col_tree = ttk.Treeview(mid, show="tree", selectmode="browse")
            self.col_tree.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(mid, orient="vertical",
                               command=self.col_tree.yview)
            sb.pack(side="left", fill="y")
            self.col_tree.configure(yscrollcommand=sb.set)
            self.col_tree.bind("<Double-1>",
                               lambda e: self._open_selected_request())

            btns = ctk.CTkFrame(root, fg_color="transparent")
            btns.pack(fill="x", pady=(12, 0))
            aura.AuraButton(btns, "Run", kind="primary",
                            command=self._run_selected_request).pack(
                side="left")
            aura.AuraButton(btns, "Open in builder", kind="secondary",
                            command=self._open_selected_request).pack(
                side="left", padx=(8, 0))
            aura.AuraButton(btns, "Delete", kind="danger",
                            command=self._delete_selected_request).pack(
                side="left", padx=(8, 0))
            aura.AuraButton(btns, "Refresh", kind="ghost",
                            command=self._reload_collections).pack(
                side="right")
            self._reload_collections()

        def _reload_collections(self):
            if not hasattr(self, "col_tree"):
                return
            self.col_tree.delete(*self.col_tree.get_children())
            self._tree_reqs = {}
            for cname in col.list_collections():
                cid = self.col_tree.insert("", "end", text=cname, open=True)
                for rname in col.list_requests(cname):
                    req = col.load_request(cname, rname)
                    iid = self.col_tree.insert(
                        cid, "end",
                        text=f"   {rname}  ·  {req.method} {req.url}")
                    self._tree_reqs[iid] = (cname, rname)
            self.env_combo.configure(
                values=["(none)"] + col.list_environments())
            active = col.active_environment()
            self.env_var.set(active if active else "(none)")

        def _selected_request(self):
            sel = self.col_tree.selection()
            if not sel or sel[0] not in getattr(self, "_tree_reqs", {}):
                self.set_error("Select a saved request first.")
                return None
            return self._tree_reqs[sel[0]]

        def _open_selected_request(self):
            got = self._selected_request()
            if not got:
                return
            try:
                req = col.load_request(*got)
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            self.show("request")
            self._load_into_builder(req)
            self.set_success(f"Loaded {got[1]!r} from {got[0]!r}.")

        def _run_selected_request(self):
            got = self._selected_request()
            if not got:
                return
            try:
                req = col.apply_environment(col.load_request(*got))
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            self.show("request")
            self._load_into_builder(req)

            def show(resp):
                self._show_response(resp)
                try:
                    hist.append(req, resp)
                except ReqBenchError:
                    pass
            self._bg(lambda: send(req), show, button=self.send_btn)

        def _delete_selected_request(self):
            got = self._selected_request()
            if not got:
                return
            if messagebox.askyesno("Delete request",
                                   f"Delete {got[1]!r} from {got[0]!r}?"):
                col.delete_request(*got)
                self._reload_collections()

        def _save_current_request(self):
            try:
                req = self._read_builder()
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            if not req.url:
                self.set_error("Enter a URL before saving.")
                return
            cname = simpledialog.askstring("Save request", "Collection name:",
                                           parent=self)
            if not cname:
                return
            rname = simpledialog.askstring("Save request", "Request name:",
                                           parent=self)
            if not rname:
                return
            try:
                col.save_request(cname, rname, req)
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            self.set_success(f"Saved {rname!r} to {cname!r}.")
            if hasattr(self, "col_tree"):
                self._reload_collections()

        def _on_env_change(self, _e=None):
            name = self.env_var.get()
            if name == "(none)":
                name = ""
            try:
                col.set_active_environment(name)
                self.set_success(f"Active environment: {name or '(none)'}.")
            except ReqBenchError as exc:
                self.set_error(str(exc))

        def _new_environment(self):
            name = simpledialog.askstring("New environment",
                                          "Environment name:", parent=self)
            if not name:
                return
            key = simpledialog.askstring(
                "New environment", "First variable name (e.g. base):",
                parent=self)
            if key:
                val = simpledialog.askstring(
                    "New environment", f"Value for {key}:", parent=self) or ""
                col.set_env_var(name, key, val)
            self._reload_collections()

        def _set_env_var(self):
            env = self.env_var.get()
            if env == "(none)" or not env:
                self.set_error("Pick an environment first.")
                return
            key = simpledialog.askstring("Set variable", "Variable name:",
                                         parent=self)
            if not key:
                return
            val = simpledialog.askstring("Set variable", f"Value for {key}:",
                                         parent=self) or ""
            col.set_env_var(env, key, val)
            self.set_success(f"{env}: {key} = {val}")

        # =================================================================
        # History section
        # =================================================================
        def _view_history(self, root):
            self.hist_list = tk.Listbox(root, activestyle="none",
                                        exportselection=False, font=(MONO, 10))
            self.hist_list.pack(fill="both", expand=True, side="top")
            aura.track(self.hist_list, "listbox")
            self.hist_list.bind("<Double-1>",
                                lambda e: self._load_history_entry())

            btns = ctk.CTkFrame(root, fg_color="transparent")
            btns.pack(fill="x", pady=(12, 0))
            aura.AuraButton(btns, "Replay", kind="primary",
                            command=self._replay_history).pack(side="left")
            aura.AuraButton(btns, "Load into builder", kind="secondary",
                            command=self._load_history_entry).pack(
                side="left", padx=(8, 0))
            aura.AuraButton(btns, "Clear history", kind="danger",
                            command=self._clear_history).pack(
                side="left", padx=(8, 0))
            aura.AuraButton(btns, "Refresh", kind="ghost",
                            command=self._reload_history).pack(side="right")
            self._reload_history()

        def _reload_history(self):
            if not hasattr(self, "hist_list"):
                return
            self.hist_list.delete(0, "end")
            self._hist_entries = hist.list_entries()
            for e in self._hist_entries:
                self.hist_list.insert(
                    "end",
                    f"{str(e.get('status')):>3}  {e.get('method'):<6} "
                    f"{e.get('url')}   ({e.get('elapsed_ms', 0):.0f} ms)")
            if not self._hist_entries:
                self.hist_list.insert("end", "(nothing sent yet)")

        def _history_index(self):
            sel = self.hist_list.curselection()
            if not sel or not getattr(self, "_hist_entries", None):
                self.set_error("Select a history entry first.")
                return None
            return sel[0]

        def _load_history_entry(self):
            idx = self._history_index()
            if idx is None:
                return
            req = self._hist_entries[idx].get("request") or {}
            self.show("request")
            self._load_into_builder(req)
            self.set_success("Loaded request into the builder.")

        def _replay_history(self):
            idx = self._history_index()
            if idx is None:
                return
            entry = self._hist_entries[idx].get("request") or {}
            self.show("request")
            self._load_into_builder(entry)

            def work():
                return hist.replay(idx)
            self._bg(work, lambda resp: (self._show_response(resp),
                                         self._reload_history()),
                     button=None)

        def _clear_history(self):
            if messagebox.askyesno("Clear history",
                                   "Delete all history entries?"):
                hist.clear()
                self._reload_history()

        # =================================================================
        # Code-gen section
        # =================================================================
        def _view_codegen(self, root):
            row = ctk.CTkFrame(root, fg_color="transparent")
            row.pack(fill="x", pady=(0, 12))
            ctk.CTkLabel(row, text="Language:", font=aura.font(),
                         text_color=aura._pair("muted")).pack(side="left")
            self.codegen_lang = tk.StringVar(value=LANGUAGES[0])
            aura.AuraCombo(row, variable=self.codegen_lang,
                           values=list(LANGUAGES), state="readonly",
                           width=180).pack(side="left", padx=8)
            aura.AuraButton(row, "Generate from current request",
                            kind="primary",
                            command=self._generate_code).pack(side="left")
            aura.AuraButton(row, "Copy", kind="secondary",
                            command=self._copy_code).pack(
                side="left", padx=(8, 0))
            self.code_txt = self._mono_text(root, wrap="none")
            self.code_txt.pack(fill="both", expand=True)

        def _generate_code(self):
            try:
                req = self._read_builder()
                snippet = generate(req, self.codegen_lang.get())
            except ReqBenchError as exc:
                self.set_error(str(exc))
                return
            self.code_txt.delete("1.0", "end")
            self.code_txt.insert("1.0", snippet)
            self.set_success(f"Generated {self.codegen_lang.get()} snippet.")

        def _copy_code(self):
            text = self.code_txt.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.set_success("Snippet copied to clipboard.")

        # =================================================================
        # About section
        # =================================================================
        def _view_about(self, root):
            card = aura.Card(root, title="About ReqBench")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="A fast, fully-offline REST & GraphQL client — build "
                     "requests, organise them into collections with "
                     "environment variables, inspect responses, replay from "
                     "history and generate client code.\n\n"
                     "100% AI-built, open source. Nothing is uploaded "
                     "anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on the permissive "
                         "'requests' library and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

        # =================================================================
        # Background runner (threaded; results marshalled via self.after)
        # =================================================================
        def _bg(self, work, on_ok, button=None, busy="Sending…"):
            if self._busy:
                self.set_error("Please wait — a request is already in flight.")
                return
            self._busy = True
            self._cancelled = False
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            try:
                self.cancel_btn.state(["!disabled"])
            except Exception:
                pass
            self.set_status(busy, kind="working")

            def run():
                try:
                    res, err = work(), None
                except ReqBenchError as ex:
                    res, err = None, str(ex)
                except Exception as ex:  # never leak a traceback
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                if button is not None:
                    try:
                        button.state(["!disabled"])
                    except Exception:
                        pass
                try:
                    self.cancel_btn.state(["disabled"])
                except Exception:
                    pass
                if self._cancelled:
                    return  # user cancelled; drop the result
                if err is not None:
                    self.set_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self.set_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
    """
    # Headless guard: without a display there is nothing to draw.
    if os.name != "nt" and not os.environ.get("DISPLAY") and sys.platform != "darwin":
        print(f"{APP_NAME}: no graphical display detected — the GUI needs a "
              f"desktop. This app is intended for the Windows desktop.")
        return 0

    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
