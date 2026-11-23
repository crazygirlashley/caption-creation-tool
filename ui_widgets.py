"""Small reusable UI helpers: hover tooltips and placeholder text.

Kept separate from caption_creator.py so the main module stays about the
app itself. Both helpers pick up the current theme's colors when shown, so
they match whichever theme is active.
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional


class Tooltip:
    """Show `text` in a small popup after hovering `widget` for `delay` ms."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        self._job = None
        if self._tip is not None or not self.text or not self.widget.winfo_viewable():
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        style = ttk.Style(self.widget)
        bg = style.lookup("TButton", "background") or "#333333"
        fg = style.lookup("TLabel", "foreground") or "#ffffff"
        border = style.lookup("TButton", "bordercolor") or "#555555"
        frame = tk.Frame(tip, bg=border, padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=self.text, bg=bg, fg=fg, justify="left", wraplength=320,
                 padx=8, pady=5).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def tooltip(widget: tk.Widget, text: str) -> tk.Widget:
    """Attach a tooltip and return the widget (handy inline)."""
    widget._tooltip = Tooltip(widget, text)  # keep a reference
    return widget


class ToggleSwitch(tk.Frame):
    """An on/off switch (pill and knob) with a label, bound to a BooleanVar.
    Click it, or focus it and press Space. Colors come from the ttk theme
    each time it's drawn, so it follows theme switches."""

    W, H = 34, 18

    def __init__(self, master: tk.Widget, text: str, variable: tk.BooleanVar,
                 bg: Optional[str] = None) -> None:
        bg = bg or master.cget("bg")
        super().__init__(master, bg=bg)
        self.var = variable
        self.canvas = tk.Canvas(self, width=self.W, height=self.H, bg=bg,
                                highlightthickness=0, cursor="hand2", takefocus=True)
        self.canvas.pack(side="left")
        self.label = ttk.Label(self, text=text, cursor="hand2")
        self.label.pack(side="left", padx=(5, 0))
        for w in (self.canvas, self.label):
            w.bind("<Button-1>", self._toggle)
        self.canvas.bind("<space>", self._toggle)
        self._trace = variable.trace_add("write", lambda *_: self.draw())
        self.bind("<Destroy>", self._untrace, add="+")
        self.draw()

    def _untrace(self, event) -> None:
        if event.widget is self:
            try:
                self.var.trace_remove("write", self._trace)
            except tk.TclError:
                pass

    def _toggle(self, _event=None) -> str:
        self.var.set(not self.var.get())
        return "break"

    def set_bg(self, bg: str) -> None:
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.draw()

    def draw(self) -> None:
        c = self.canvas
        try:
            on = bool(self.var.get())
        except tk.TclError:
            on = False
        style = ttk.Style(self)
        on_fill = style.lookup("Accent.TButton", "background") or "#d0459a"
        off_fill = style.lookup("TButton", "bordercolor") or "#888888"
        knob = "#ffffff"
        c.delete("all")
        w, h, r = self.W, self.H, self.H // 2
        fill = on_fill if on else off_fill
        c.create_oval(0, 0, h, h, fill=fill, outline=fill)
        c.create_oval(w - h, 0, w, h, fill=fill, outline=fill)
        c.create_rectangle(r, 0, w - r, h, fill=fill, outline=fill)
        x = w - r if on else r
        c.create_oval(x - r + 3, 3, x + r - 3, h - 3, fill=knob, outline=knob)


class Placeholder:
    """Grey hint text shown over an empty tk.Text (e.g. "Start writing…")."""

    def __init__(self, text_widget: tk.Text, hint: str) -> None:
        self.box = text_widget
        self.label = tk.Label(text_widget, text=hint, justify="left", anchor="nw",
                              cursor="xterm", wraplength=520)
        self.label.bind("<Button-1>", lambda _e: text_widget.focus_set())
        for seq in ("<KeyRelease>", "<FocusIn>", "<FocusOut>", "<<Modified>>"):
            text_widget.bind(seq, lambda _e: self.update(), add="+")
        self.update()

    def update(self) -> None:
        empty = not self.box.get("1.0", "end-1c").strip()
        if empty:
            try:
                bg = self.box.cget("bg")
                fg = ttk.Style(self.box).lookup("Muted.TLabel", "foreground") or "#888888"
            except tk.TclError:
                bg, fg = "#ffffff", "#888888"
            font = self.box.cget("font")
            self.label.configure(bg=bg, fg=fg, font=font)
            padx = int(str(self.box.cget("padx"))) + 2
            pady = int(str(self.box.cget("pady")))
            self.label.place(x=padx, y=pady)
        else:
            self.label.place_forget()
