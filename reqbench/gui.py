#!/usr/bin/env python3
r"""ReqBench -- a pure-stdlib tkinter GUI on top of the ``reqbench`` API.

A single main window: a left sidebar (Request, Collections, History, Code-gen)
and a main panel that swaps to the selected view.  The Request builder composes
a method/URL/headers/params/body/auth request, sends it on a background thread
(so the UI never freezes), and shows the parsed response -- pretty JSON, headers
and timing -- inline.  Failures surface as the :class:`ReqBenchError` message in
a result bar, never as a raw traceback.

Design goals mirror the rest of QuickOpen's desktop apps:
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a note, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import json
import os
import sys
import threading

# tkinter is imported lazily inside build_app()/main() so that merely importing
# this module (during packaging or on a headless CI box) never fails.

APP_NAME = "ReqBench"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "ReqBench — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
FONT = "Segoe UI"
MONO = "Consolas"

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}

# Sidebar sections: (view_id, label).
VIEWS = [
    ("request", "  Request"),
    ("collections", "  Collections"),
    ("history", "  History"),
    ("codegen", "  Code-gen"),
]

VIEW_META = {
    "request": ("Request builder",
                "Compose a REST or GraphQL request, send it, and inspect the "
                "response — pretty JSON, headers and timing."),
    "collections": ("Collections & environments",
                    "Save requests into named collections and switch the active "
                    "environment; {{variables}} are filled in on send."),
    "history": ("History",
                "Every request you send is logged here — select one to replay it "
                "or load it back into the builder."),
    "codegen": ("Code generator",
                "Turn the current request into a copy-paste snippet: curl, "
                "Python requests, or JavaScript fetch."),
}


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
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog

    from . import guiconfig
    from . import collections as col
    from . import history as hist
    from .codegen import generate, LANGUAGES
    from .errors import ReqBenchError
    from .graphql import build_graphql_request
    from .http import send
    from .model import Request, Response, METHODS

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

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1120x720")
            self.minsize(940, 600)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._cancelled = False
            self._panels = {}
            self._current = None
            self._current_id = None
            self._tracked = []     # (widget, role) for manual re-theming
            self._img_refs = []
            self._spin_job = None
            self._spin_i = 0

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(40, lambda: self._select_view("request"))

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("req-bench.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("req-bench.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("Card.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"],
                            font=(FONT, 10))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 13, "bold"))
            style.configure("Ok.TLabel", background=p["bg"], foreground=p["ok"])
            style.configure("Err.TLabel", background=p["bg"], foreground=p["err"])
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(14, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            style.configure("Nav.TButton", background=p["surface"],
                            foreground=p["text"], anchor="w", padding=(14, 9),
                            font=(FONT, 11))
            style.map("Nav.TButton", background=[("active", p["trough"])])
            style.configure("NavSel.TButton", background=p["primary"],
                            foreground="#ffffff", anchor="w", padding=(14, 9),
                            font=(FONT, 11, "bold"))
            style.map("NavSel.TButton",
                      background=[("active", p["primary_hi"])])
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("TRadiobutton", background=p["bg"], foreground=p["text"])
            style.map("TRadiobutton", background=[("active", p["bg"])])
            style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
            style.configure("TNotebook.Tab", background=p["surface"],
                            foreground=p["muted"], padding=(12, 6))
            style.map("TNotebook.Tab",
                      background=[("selected", p["bg"])],
                      foreground=[("selected", p["text"])])
            style.configure("TLabelframe", background=p["bg"], foreground=p["text"],
                            bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=24)
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role in ("text", "mono"):
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                except Exception:
                    pass
            self._refresh_nav_styles()

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Send request", accelerator="Ctrl+Enter",
                              command=self._send_current)
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-Return>", lambda e: self._send_current())

        # ---- layout
        def _build_layout(self):
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(14, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="ReqBench", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="   offline · open source · by QuickOpen"
                      ).pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")

            body = ttk.Frame(self, style="TFrame")
            body.pack(fill="both", expand=True)

            side = ttk.Frame(body, style="Sidebar.TFrame", width=190)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            self._nav_btns = {}
            for vid, label in VIEWS:
                b = ttk.Button(side, text=label, style="Nav.TButton",
                               command=lambda v=vid: self._select_view(v))
                b.pack(fill="x", padx=8, pady=(8 if not self._nav_btns else 2, 0))
                self._nav_btns[vid] = b

            main = ttk.Frame(body, style="TFrame", padding=(16, 12))
            main.pack(side="left", fill="both", expand=True)

            head = ttk.Frame(main, style="TFrame")
            head.pack(fill="x")
            self.title_lbl = ttk.Label(head, text="", style="Header.TLabel")
            self.title_lbl.pack(anchor="w")
            self.desc_lbl = ttk.Label(head, text="", style="Sub.TLabel",
                                      wraplength=820, justify="left")
            self.desc_lbl.pack(anchor="w", pady=(2, 8))
            ttk.Separator(main).pack(fill="x")

            self.container = ttk.Frame(main, style="TFrame")
            self.container.pack(fill="both", expand=True, pady=(10, 8))

            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.spin_lbl = ttk.Label(bar, text="", style="Status.TLabel", width=3)
            self.spin_lbl.pack(side="left")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        anchor="w", wraplength=820, justify="left")
            self.status_lbl.pack(side="left", fill="x", expand=True, padx=6)

        def _refresh_nav_styles(self):
            for vid, btn in getattr(self, "_nav_btns", {}).items():
                btn.configure(style="NavSel.TButton" if vid == self._current_id
                              else "Nav.TButton")

        # ---- view switching
        def _select_view(self, view_id):
            if self._current is not None:
                self._current.pack_forget()
            panel = self._panels.get(view_id)
            if panel is None:
                panel = ttk.Frame(self.container, style="TFrame")
                getattr(self, "_view_" + view_id)(panel)
                self._panels[view_id] = panel
                self._apply_theme()
            panel.pack(fill="both", expand=True)
            self._current = panel
            self._current_id = view_id
            title, desc = VIEW_META[view_id]
            self.title_lbl.configure(text=title)
            self.desc_lbl.configure(text=desc)
            self._refresh_nav_styles()
            if view_id == "collections":
                self._reload_collections()
            elif view_id == "history":
                self._reload_history()

        # =================================================================
        # Request builder view
        # =================================================================
        def _view_request(self, root):
            # URL row
            urlrow = ttk.Frame(root, style="TFrame")
            urlrow.pack(fill="x")
            self.method_var = tk.StringVar(value="GET")
            ttk.Combobox(urlrow, textvariable=self.method_var, values=METHODS,
                         state="readonly", width=9).pack(side="left")
            self.url_var = tk.StringVar()
            url_entry = ttk.Entry(urlrow, textvariable=self.url_var)
            url_entry.pack(side="left", fill="x", expand=True, padx=6)
            url_entry.bind("<Return>", lambda e: self._send_current())
            self.send_btn = ttk.Button(urlrow, text="Send", style="Accent.TButton",
                                       command=self._send_current)
            self.send_btn.pack(side="left")
            self.cancel_btn = ttk.Button(urlrow, text="Cancel",
                                         command=self._cancel_send, state="disabled")
            self.cancel_btn.pack(side="left", padx=(6, 0))

            # request tabs
            nb = ttk.Notebook(root)
            nb.pack(fill="x", pady=(10, 8))

            params_tab = ttk.Frame(nb, style="TFrame", padding=8)
            nb.add(params_tab, text="Params")
            ttk.Label(params_tab, style="Sub.TLabel",
                      text="One 'key = value' per line.").pack(anchor="w")
            self.params_txt = tk.Text(params_tab, height=4, wrap="none",
                                      font=(MONO, 10))
            self.params_txt.pack(fill="x", pady=(4, 0))
            self.track(self.params_txt, "mono")

            headers_tab = ttk.Frame(nb, style="TFrame", padding=8)
            nb.add(headers_tab, text="Headers")
            ttk.Label(headers_tab, style="Sub.TLabel",
                      text="One 'Name: value' per line.").pack(anchor="w")
            self.headers_txt = tk.Text(headers_tab, height=4, wrap="none",
                                       font=(MONO, 10))
            self.headers_txt.pack(fill="x", pady=(4, 0))
            self.track(self.headers_txt, "mono")

            body_tab = ttk.Frame(nb, style="TFrame", padding=8)
            nb.add(body_tab, text="Body")
            self.body_type = tk.StringVar(value="none")
            brow = ttk.Frame(body_tab, style="TFrame")
            brow.pack(fill="x")
            for val, lbl in [("none", "None"), ("json", "JSON"),
                             ("form", "Form"), ("raw", "Raw")]:
                ttk.Radiobutton(brow, text=lbl, value=val,
                                variable=self.body_type).pack(side="left", padx=(0, 10))
            ttk.Label(body_tab, style="Sub.TLabel",
                      text="JSON: an object · Form: 'key = value' lines · Raw: verbatim"
                      ).pack(anchor="w", pady=(4, 0))
            self.body_txt = tk.Text(body_tab, height=6, wrap="word", font=(MONO, 10))
            self.body_txt.pack(fill="both", expand=True, pady=(4, 0))
            self.track(self.body_txt, "mono")

            auth_tab = ttk.Frame(nb, style="TFrame", padding=8)
            nb.add(auth_tab, text="Auth")
            self.auth_type = tk.StringVar(value="none")
            arow = ttk.Frame(auth_tab, style="TFrame")
            arow.pack(fill="x")
            for val, lbl in [("none", "None"), ("basic", "Basic"),
                             ("bearer", "Bearer")]:
                ttk.Radiobutton(arow, text=lbl, value=val,
                                variable=self.auth_type).pack(side="left", padx=(0, 10))
            grid = ttk.Frame(auth_tab, style="TFrame")
            grid.pack(fill="x", pady=(6, 0))
            ttk.Label(grid, text="User / Token", style="Sub.TLabel").grid(
                row=0, column=0, sticky="w", padx=(0, 6))
            self.auth_user = tk.StringVar()
            ttk.Entry(grid, textvariable=self.auth_user, width=32).grid(
                row=0, column=1, sticky="w")
            ttk.Label(grid, text="Password", style="Sub.TLabel").grid(
                row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
            self.auth_pass = tk.StringVar()
            ttk.Entry(grid, textvariable=self.auth_pass, show="•", width=32).grid(
                row=1, column=1, sticky="w", pady=(4, 0))

            # save-to-collection row
            saverow = ttk.Frame(root, style="TFrame")
            saverow.pack(fill="x", pady=(0, 6))
            ttk.Button(saverow, text="Save to collection…",
                       command=self._save_current_request).pack(side="left")
            ttk.Button(saverow, text="Clear",
                       command=self._clear_builder).pack(side="left", padx=6)

            # response viewer
            ttk.Separator(root).pack(fill="x")
            self.resp_status = ttk.Label(root, text="No response yet.",
                                         style="Sub.TLabel")
            self.resp_status.pack(anchor="w", pady=(8, 4))
            rnb = ttk.Notebook(root)
            rnb.pack(fill="both", expand=True)
            rb = ttk.Frame(rnb, style="TFrame", padding=4)
            rnb.add(rb, text="Body")
            self.resp_body = tk.Text(rb, wrap="none", font=(MONO, 10))
            self.resp_body.pack(fill="both", expand=True)
            self.track(self.resp_body, "mono")
            rh = ttk.Frame(rnb, style="TFrame", padding=4)
            rnb.add(rh, text="Headers")
            self.resp_headers = tk.Text(rh, wrap="none", font=(MONO, 10))
            self.resp_headers.pack(fill="both", expand=True)
            self.track(self.resp_headers, "mono")

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
                url=self.url_var.get().strip(),
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
            self.url_var.set(req.url)
            self.headers_txt.delete("1.0", "end")
            self.headers_txt.insert("1.0", _kv_to_lines(req.headers, ":"))
            self.params_txt.delete("1.0", "end")
            self.params_txt.insert("1.0", _kv_to_lines(req.params, "="))
            self.body_type.set(req.body_type)
            self.body_txt.delete("1.0", "end")
            if req.body_type == "json" and req.body is not None:
                self.body_txt.insert("1.0", json.dumps(req.body, indent=2))
            elif req.body_type == "form" and isinstance(req.body, dict):
                self.body_txt.insert("1.0", _kv_to_lines(req.body, "="))
            elif req.body is not None:
                self.body_txt.insert("1.0", str(req.body))
            self.auth_type.set(req.auth_type)
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
            self.resp_status.configure(text="No response yet.")
            self.resp_body.delete("1.0", "end")
            self.resp_headers.delete("1.0", "end")

        def _send_current(self):
            if self._current_id != "request":
                self._select_view("request")
            try:
                req = col.apply_environment(self._read_builder())
            except ReqBenchError as exc:
                self._set_status(str(exc), kind="err")
                return
            if not req.url:
                self._set_status("Enter a URL first.", kind="err")
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
                self._set_status("Cancelled — ignoring the in-flight response.",
                                 kind="err")

        def _show_response(self, resp):
            p = self._pal()
            color = p["ok"] if resp.ok else (p["err"] if resp.status >= 400
                                             else p["muted"])
            self.resp_status.configure(
                text=f"{resp.status} {resp.reason}   ·   {resp.elapsed_ms:.0f} ms"
                     f"   ·   {human_size(resp.size)}",
                foreground=color)
            self.resp_body.delete("1.0", "end")
            self.resp_body.insert("1.0", resp.pretty_body())
            self.resp_headers.delete("1.0", "end")
            self.resp_headers.insert(
                "1.0", "\n".join(f"{k}: {v}" for k, v in resp.headers.items()))
            self._set_status(f"Done — {resp.status} in {resp.elapsed_ms:.0f} ms.",
                             kind="ok")

        # =================================================================
        # Collections view
        # =================================================================
        def _view_collections(self, root):
            envrow = ttk.Frame(root, style="TFrame")
            envrow.pack(fill="x", pady=(0, 8))
            ttk.Label(envrow, text="Active environment:",
                      style="Sub.TLabel").pack(side="left")
            self.env_var = tk.StringVar()
            self.env_combo = ttk.Combobox(envrow, textvariable=self.env_var,
                                          state="readonly", width=24)
            self.env_combo.pack(side="left", padx=6)
            self.env_combo.bind("<<ComboboxSelected>>", self._on_env_change)
            ttk.Button(envrow, text="New env…",
                       command=self._new_environment).pack(side="left")
            ttk.Button(envrow, text="Set variable…",
                       command=self._set_env_var).pack(side="left", padx=6)

            mid = ttk.Frame(root, style="TFrame")
            mid.pack(fill="both", expand=True)
            self.col_tree = ttk.Treeview(mid, show="tree", selectmode="browse")
            self.col_tree.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(mid, orient="vertical", command=self.col_tree.yview)
            sb.pack(side="left", fill="y")
            self.col_tree.configure(yscrollcommand=sb.set)
            self.col_tree.bind("<Double-1>", lambda e: self._open_selected_request())

            btns = ttk.Frame(root, style="TFrame")
            btns.pack(fill="x", pady=(8, 0))
            ttk.Button(btns, text="Open in builder",
                       command=self._open_selected_request).pack(side="left")
            ttk.Button(btns, text="Run", style="Accent.TButton",
                       command=self._run_selected_request).pack(side="left", padx=6)
            ttk.Button(btns, text="Delete",
                       command=self._delete_selected_request).pack(side="left")
            ttk.Button(btns, text="Refresh",
                       command=self._reload_collections).pack(side="right")

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
                        cid, "end", text=f"   {rname}  ·  {req.method} {req.url}")
                    self._tree_reqs[iid] = (cname, rname)
            envs = [""] + col.list_environments()
            self.env_combo.configure(values=["(none)"] + col.list_environments())
            active = col.active_environment()
            self.env_var.set(active if active else "(none)")

        def _selected_request(self):
            sel = self.col_tree.selection()
            if not sel or sel[0] not in getattr(self, "_tree_reqs", {}):
                self._set_status("Select a saved request first.", kind="err")
                return None
            return self._tree_reqs[sel[0]]

        def _open_selected_request(self):
            got = self._selected_request()
            if not got:
                return
            try:
                req = col.load_request(*got)
            except ReqBenchError as exc:
                self._set_status(str(exc), kind="err")
                return
            self._select_view("request")
            self._load_into_builder(req)
            self._set_status(f"Loaded {got[1]!r} from {got[0]!r}.", kind="ok")

        def _run_selected_request(self):
            got = self._selected_request()
            if not got:
                return
            try:
                req = col.apply_environment(col.load_request(*got))
            except ReqBenchError as exc:
                self._set_status(str(exc), kind="err")
                return
            self._select_view("request")
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
                self._set_status(str(exc), kind="err")
                return
            if not req.url:
                self._set_status("Enter a URL before saving.", kind="err")
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
                self._set_status(str(exc), kind="err")
                return
            self._set_status(f"Saved {rname!r} to {cname!r}.", kind="ok")
            if self._panels.get("collections"):
                self._reload_collections()

        def _on_env_change(self, _e=None):
            name = self.env_var.get()
            if name == "(none)":
                name = ""
            try:
                col.set_active_environment(name)
                self._set_status(f"Active environment: {name or '(none)'}.",
                                 kind="ok")
            except ReqBenchError as exc:
                self._set_status(str(exc), kind="err")

        def _new_environment(self):
            name = simpledialog.askstring("New environment", "Environment name:",
                                          parent=self)
            if not name:
                return
            key = simpledialog.askstring(
                "New environment", "First variable name (e.g. base):", parent=self)
            if key:
                val = simpledialog.askstring(
                    "New environment", f"Value for {key}:", parent=self) or ""
                col.set_env_var(name, key, val)
            self._reload_collections()

        def _set_env_var(self):
            env = self.env_var.get()
            if env == "(none)" or not env:
                self._set_status("Pick an environment first.", kind="err")
                return
            key = simpledialog.askstring("Set variable", "Variable name:",
                                         parent=self)
            if not key:
                return
            val = simpledialog.askstring("Set variable", f"Value for {key}:",
                                         parent=self) or ""
            col.set_env_var(env, key, val)
            self._set_status(f"{env}: {key} = {val}", kind="ok")

        # =================================================================
        # History view
        # =================================================================
        def _view_history(self, root):
            self.hist_list = tk.Listbox(root, activestyle="none", font=(MONO, 10))
            self.hist_list.pack(fill="both", expand=True, side="top")
            self.track(self.hist_list, "listbox")
            self.hist_list.bind("<Double-1>", lambda e: self._load_history_entry())

            btns = ttk.Frame(root, style="TFrame")
            btns.pack(fill="x", pady=(8, 0))
            ttk.Button(btns, text="Replay", style="Accent.TButton",
                       command=self._replay_history).pack(side="left")
            ttk.Button(btns, text="Load into builder",
                       command=self._load_history_entry).pack(side="left", padx=6)
            ttk.Button(btns, text="Clear history",
                       command=self._clear_history).pack(side="left")
            ttk.Button(btns, text="Refresh",
                       command=self._reload_history).pack(side="right")

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
                self._set_status("Select a history entry first.", kind="err")
                return None
            return sel[0]

        def _load_history_entry(self):
            idx = self._history_index()
            if idx is None:
                return
            req = self._hist_entries[idx].get("request") or {}
            self._select_view("request")
            self._load_into_builder(req)
            self._set_status("Loaded request into the builder.", kind="ok")

        def _replay_history(self):
            idx = self._history_index()
            if idx is None:
                return
            self._select_view("request")
            self._load_into_builder(self._hist_entries[idx].get("request") or {})

            def work():
                return hist.replay(idx)
            self._bg(work, lambda resp: (self._show_response(resp),
                                         self._reload_history()),
                     button=None)

        def _clear_history(self):
            if messagebox.askyesno("Clear history", "Delete all history entries?"):
                hist.clear()
                self._reload_history()

        # =================================================================
        # Code-gen view
        # =================================================================
        def _view_codegen(self, root):
            row = ttk.Frame(root, style="TFrame")
            row.pack(fill="x", pady=(0, 8))
            ttk.Label(row, text="Language:", style="Sub.TLabel").pack(side="left")
            self.codegen_lang = tk.StringVar(value=LANGUAGES[0])
            ttk.Combobox(row, textvariable=self.codegen_lang, values=LANGUAGES,
                         state="readonly", width=16).pack(side="left", padx=6)
            ttk.Button(row, text="Generate from current request",
                       style="Accent.TButton",
                       command=self._generate_code).pack(side="left")
            ttk.Button(row, text="Copy",
                       command=self._copy_code).pack(side="left", padx=6)
            self.code_txt = tk.Text(root, wrap="none", font=(MONO, 10))
            self.code_txt.pack(fill="both", expand=True)
            self.track(self.code_txt, "mono")

        def _generate_code(self):
            try:
                req = self._read_builder()
                snippet = generate(req, self.codegen_lang.get())
            except ReqBenchError as exc:
                self._set_status(str(exc), kind="err")
                return
            self.code_txt.delete("1.0", "end")
            self.code_txt.insert("1.0", snippet)
            self._set_status(f"Generated {self.codegen_lang.get()} snippet.",
                             kind="ok")

        def _copy_code(self):
            text = self.code_txt.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                self._set_status("Snippet copied to clipboard.", kind="ok")

        # =================================================================
        # Background runner + status/spinner
        # =================================================================
        def _bg(self, work, on_ok, button=None, busy="Sending…"):
            if self._busy:
                self._set_status("Please wait — a request is already in flight.",
                                 kind="err")
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
            self._set_status(busy, kind="working")
            self._start_spinner()

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
                self._stop_spinner()
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
                    self._set_status(err, kind="err")
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self._set_status(f"Post-processing error: {ex}", kind="err")

            threading.Thread(target=run, daemon=True).start()

        def _start_spinner(self):
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

            def tick():
                self._spin_i = (self._spin_i + 1) % len(frames)
                self.spin_lbl.configure(text=frames[self._spin_i])
                self._spin_job = self.after(90, tick)
            self._stop_spinner()
            tick()

        def _stop_spinner(self):
            if self._spin_job is not None:
                try:
                    self.after_cancel(self._spin_job)
                except Exception:
                    pass
                self._spin_job = None
            self.spin_lbl.configure(text="")

        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        # ---- About
        def _about(self):
            win = tk.Toplevel(self)
            win.title("About ReqBench")
            win.configure(bg=self._pal()["bg"])
            win.resizable(False, False)
            frm = ttk.Frame(win, style="TFrame", padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="ReqBench", style="Header.TLabel").pack(anchor="w")
            ttk.Label(frm, text=f"Version {APP_VERSION}",
                      style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
            ttk.Label(frm, style="TLabel", justify="left", wraplength=380,
                      text="A fast, fully-offline REST & GraphQL client — build "
                           "requests, organise them into collections with "
                           "environment variables, inspect responses, replay from "
                           "history and generate client code.\n\n"
                           "100% AI-built, open source. Nothing is uploaded "
                           "anywhere.").pack(anchor="w")
            ttk.Label(frm, style="Sub.TLabel", justify="left", wraplength=380,
                      text="Licensed under Apache-2.0. Built on the permissive "
                           "'requests' library.").pack(anchor="w", pady=(8, 4))
            link = ttk.Label(frm, text="Project page: quickopen.ai",
                             style="Ok.TLabel", cursor="hand2")
            link.pack(anchor="w", pady=(4, 10))
            link.bind("<Button-1>", lambda e: open_with_default_app(PROJECT_URL))
            ttk.Button(frm, text="Close", command=win.destroy).pack(anchor="e")

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server), it prints a friendly note and returns 0
    instead of raising.
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
