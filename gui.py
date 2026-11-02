"""Control panel for the ASCII clock: colours, spacing, wallpaper.

Run with:  python gui.py     (or pythonw gui.py for no console window)

The preview is live and costs nothing -- it recolours a cached crop of the
source art in memory, so you can flick through presets before committing to
one. Applying is the slow part, because it rebuilds a 2880x1800 wallpaper, so
it runs on a worker thread and reports back through the status line.

Spacing sliders push straight into the running skin as you drag, which is
runtime-only. "Apply & save" is what writes them into AsciiClock.ini.
"""

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

import extract_digits
import palette
import skin

# Preview geometry, all in the wallpaper's own 1920x1080 space.
CROP = (480, 270, 1440, 810)
PREVIEW_SCALE = 0.62
# Where the clock sits inside that crop, derived from the real screen rect.
CLOCK_BOX = (771, 498, 378, 84)

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"


class App(tk.Tk):
    def __init__(self, show=True):
        super().__init__()
        # Stay hidden until the widget tree and first preview are built, so no
        # half-drawn window flashes. show=False keeps it hidden for testing.
        self.withdraw()
        self.title("ASCII Clock Control")
        # Height is adjustable: the control area scrolls, so a shorter window
        # is usable. Width is fixed by the preview.
        self.resizable(False, True)

        self.palettes = palette.load_palettes()
        self.config_data = palette.load_config()
        self.jobs = queue.Queue()
        self._busy = False

        self._load_sources()
        self._build()
        self._refresh_preview()
        self._fit_to_screen()
        self.after(100, self._drain)
        if show:
            self.deiconify()

    # -------------------------------------------------------------- sources --

    def _load_sources(self):
        """Cache the pristine art the preview is rebuilt from."""
        src = Image.open(palette.WALL_SRC).convert("RGB")
        w, h = src.size
        cx, cy = self.palettes["sources"]["wallpaper"]["ring_centre"]
        dx, dy = round(w / 2 - cx), round(h / 2 - cy)
        box = (CROP[0] - dx, CROP[1] - dy, CROP[2] - dx, CROP[3] - dy)
        self.wall_crop = src.crop(box)
        self.wall_ramp = [palette.from_hex(c)
                          for c in self.palettes["sources"]["wallpaper"]["ramp"]]
        self._scan_glyphs()
        self._reload_digits()

    def _scan_glyphs(self):
        """What each digit could be drawn from, and what it currently is.

        ORDER stands in for the sliced sheet: options_for only needs to know
        whether the digit has a cell there, so there is no need to open it.
        """
        self.sources = extract_digits.scan_sources()
        self.glyph_options = {
            d: extract_digits.options_for(d, self.sources, extract_digits.ORDER)
            for d in extract_digits.DIGITS
        }
        self.glyph_choice = extract_digits.resolve_choices(
            self.sources, extract_digits.ORDER,
            self.config_data.get("glyphs", {}),
        )

    def _reload_digits(self):
        self.digits = {
            str(d): Image.open(palette.DIGITS_DIR / f"{d}.png").convert("RGBA")
            for d in range(10)
        }
        # Same images keyed the way morph.py wants them, and a clean slate for
        # the changed-cell counts, which are a property of these exact glyphs.
        self.digits_by_n = {int(k): v for k, v in self.digits.items()}
        self._changed_cache = {}
        self.digit_ramp = palette.digits_ramp()

    # -------------------------------------------------------------- widgets --

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # The preview is pinned: it lives outside the scrolling area so it
        # stays visible while the controls below it scroll under it.
        head = ttk.Frame(self, padding=(10, 10, 10, 0))
        head.grid(row=0, column=0, sticky="ew")
        self.preview = tk.Label(head, bd=1, relief="sunken", background="#000")
        self.preview.pack()

        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=bar.set)

        outer = ttk.Frame(self.canvas, padding=10)
        self._inner = outer
        win = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        # keep the inner frame as wide as the canvas so nothing is clipped
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(win, width=e.width))

        # --- colour ---------------------------------------------------------
        col = ttk.LabelFrame(outer, text="Colour preset", padding=8)
        col.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.preset_var = tk.StringVar(value=self.config_data["preset"])
        names = list(self.palettes["presets"])
        combo = ttk.Combobox(col, textvariable=self.preset_var, values=names,
                             state="readonly", width=22)
        combo.grid(row=0, column=0, sticky="w")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())

        self.swatch = tk.Canvas(col, width=210, height=22, highlightthickness=0)
        self.swatch.grid(row=0, column=1, padx=10)

        self.to_wall = tk.BooleanVar(value=self.config_data["apply_to"]["wallpaper"])
        self.to_dig = tk.BooleanVar(value=self.config_data["apply_to"]["digits"])
        ttk.Checkbutton(col, text="Wallpaper", variable=self.to_wall,
                        command=self._refresh_preview).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(col, text="Clock digits", variable=self.to_dig,
                        command=self._refresh_preview).grid(
            row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(col, text="Upscaling").grid(row=2, column=0, sticky="w",
                                              pady=(6, 0))
        self.resample_var = tk.StringVar(value=self.config_data["resample"])
        ttk.Combobox(col, textvariable=self.resample_var,
                     values=list(palette.RESAMPLERS), state="readonly",
                     width=22).grid(row=3, column=0, sticky="w")
        ttk.Label(col, foreground="#666", wraplength=200, justify="left",
                  text="lanczos_quantize keeps the art at 8 colours; "
                       "lanczos alone interpolates ~10k."
                  ).grid(row=2, column=1, rowspan=2, sticky="w", padx=10)

        # --- spacing --------------------------------------------------------
        sp = ttk.LabelFrame(outer, text="Spacing (live in the skin)", padding=8)
        sp.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.current = skin.read_vars()
        self.vars = {}
        for r, name in enumerate(n for n in skin.TUNABLES
                                 if n not in skin.ELSEWHERE):
            self._slider(sp, name, r)

        # --- sweep ----------------------------------------------------------
        sw = ttk.LabelFrame(outer, text="Minute sweep", padding=8)
        sw.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.morph_var = tk.BooleanVar(
            value=self.current.get("Morph", "1").strip() == "1")
        ttk.Checkbutton(sw, text="Dissolve digits as they change",
                        variable=self.morph_var,
                        command=self._refresh_preview).grid(
            row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(sw, text="Step").grid(row=1, column=0, sticky="w")
        self.cell_var = tk.StringVar(
            value=self.current.get("MorphCell", "8").strip())
        cell = ttk.Combobox(sw, textvariable=self.cell_var, state="readonly",
                            width=6, values=list(skin.CHOICES["MorphCell"][0]))
        cell.grid(row=1, column=1, sticky="w", padx=(6, 0))
        cell.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttk.Label(sw, foreground="#666",
                  text="8px = 2x2 characters per step, 4px = one character"
                  ).grid(row=1, column=2, columnspan=2, sticky="w", padx=8)

        ttk.Label(sw, text="Rate").grid(row=2, column=0, sticky="w")
        self.rate_var = tk.StringVar(value=str(skin.read_update_ms()))
        rate = ttk.Combobox(sw, textvariable=self.rate_var, state="readonly",
                            width=6, values=[str(r) for r in skin.RATES])
        rate.grid(row=2, column=1, sticky="w", padx=(6, 0))
        rate.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttk.Label(sw, foreground="#666",
                  text="Skin tick, ms -- how often the sweep can step. "
                       "Needs Apply."
                  ).grid(row=2, column=2, columnspan=2, sticky="w", padx=8)

        # How long the dissolve gets. Lives here rather than in the spacing
        # block because it is only meaningful alongside the three above.
        self._slider(sw, "MorphSecs", 3, label="Window")
        self.pace = tk.StringVar()
        ttk.Label(sw, textvariable=self.pace, foreground="#444").grid(
            row=4, column=1, columnspan=3, sticky="w", padx=6)

        # Preview-only: the fastest way to judge the sweep without waiting a
        # real minute. Never written to the skin.
        ttk.Label(sw, text="Scrub").grid(row=5, column=0, sticky="w")
        self.frame_var = tk.IntVar(value=0)
        fs = ttk.Scale(sw, from_=0, to=59, length=200,
                       command=lambda v: self._on_scrub(v))
        fs.grid(row=5, column=1, padx=6, sticky="w")
        ttk.Label(sw, textvariable=self.frame_var, width=4).grid(row=5, column=2)
        ttk.Label(sw, text="Preview only -- never written to the skin",
                  foreground="#666").grid(row=5, column=3, sticky="w")

        # --- glyph sources --------------------------------------------------
        gl = ttk.LabelFrame(outer, text="Glyph source per digit", padding=8)
        gl.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.glyph_vars, self.glyph_boxes = {}, {}
        for i, d in enumerate(extract_digits.DIGITS):
            r, c = divmod(i, 5)
            cell = ttk.Frame(gl)
            cell.grid(row=r, column=c, padx=(0, 8), pady=2, sticky="w")
            ttk.Label(cell, text=d, width=2).grid(row=0, column=0)
            v = tk.StringVar(value=self.glyph_choice[d])
            self.glyph_vars[d] = v
            box = ttk.Combobox(cell, textvariable=v, state="readonly", width=11,
                               values=self.glyph_options[d])
            box.grid(row=0, column=1)
            box.bind("<<ComboboxSelected>>",
                     lambda _e, digit=d: self._on_glyph(digit))
            self.glyph_boxes[d] = box
        ttk.Label(gl, foreground="#666",
                  text="Drop 80x80 PNGs into source_digits/ -- a file offers "
                       "the first digit in its name.").grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # --- time / debug ---------------------------------------------------
        dbg = ttk.LabelFrame(outer, text="Preview time", padding=8)
        dbg.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.time_var = tk.StringVar(value=datetime.now().strftime("%H:%M"))
        e = ttk.Entry(dbg, textvariable=self.time_var, width=8)
        e.grid(row=0, column=0)
        e.bind("<KeyRelease>", lambda _e: self._refresh_preview())
        ttk.Button(dbg, text="Now", command=self._now).grid(row=0, column=1,
                                                            padx=4)
        ttk.Button(dbg, text="11:11", command=lambda: self._set_time("11:11")
                   ).grid(row=0, column=2)
        ttk.Button(dbg, text="Toggle skin debug clock",
                   command=self._toggle_debug).grid(row=0, column=3, padx=(12, 0))

        # --- actions --------------------------------------------------------
        act = ttk.Frame(outer)
        act.grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Button(act, text="Apply & save", command=self._apply).grid(
            row=0, column=0)
        ttk.Button(act, text="Rebuild digits from source",
                   command=self._rebuild).grid(row=0, column=1, padx=6)
        ttk.Button(act, text="Refresh skin", command=self._refresh_skin).grid(
            row=0, column=2)

        self.status = tk.StringVar(value="ready")
        ttk.Label(outer, textvariable=self.status, foreground="#444").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self._bind_wheel()

    def _slider(self, parent, name, row, label=None):
        """One skin variable as label / scale / value / hint across four cols."""
        lo, hi, tip = skin.TUNABLES[name]
        try:
            start = int(float(self.current.get(name, lo)))
        except ValueError:
            start = lo

        v = tk.IntVar(value=start)
        self.vars[name] = v
        ttk.Label(parent, text=label or name).grid(row=row, column=0, sticky="w")
        # Deliberately not bound with variable=: ttk.Scale writes floats, and
        # IntVar.get() raises on a float-formatted value.
        sc = ttk.Scale(parent, from_=lo, to=hi, length=200,
                       command=lambda val, n=name: self._on_slide(n, val))
        sc.set(start)
        sc.grid(row=row, column=1, padx=6)
        ttk.Label(parent, textvariable=v, width=4).grid(row=row, column=2)
        ttk.Label(parent, text=tip, foreground="#666").grid(
            row=row, column=3, sticky="w")

    def _changed(self, cell):
        """Mean cells a transition alters at this cell size, cached."""
        if cell not in self._changed_cache:
            import morph
            self._changed_cache[cell] = morph.changed_cells(cell, self.digits_by_n)
        return self._changed_cache[cell]

    def _describe_pace(self):
        """Restate the settings as what you will actually see, per step.

        The honest denominator is changed cells, not the whole grid: unchanged
        cells are not part of the reveal, so quoting the grid would overstate
        how much moves. Steps beyond the changed-cell count cannot all show
        something, which is the warning case.
        """
        secs = self.vars["MorphSecs"].get()
        cell = int(self.cell_var.get())
        tps = 1000 // int(self.rate_var.get())
        changed = self._changed(cell)

        # Frame 0 lands on the window's first tick and frame 59 on its last,
        # so N ticks are N-1 actual moves.
        steps = max(1, secs * tps - 1)
        per = changed / steps
        note = "" if per >= 1 else "  -- more steps than cells, some will stall"
        self.pace.set(f"{steps} steps of ~{per:.1f} cells "
                      f"({changed} of {(80 // cell) ** 2} change){note}")

    def _bind_wheel(self):
        """Wheel scrolls the control area from anywhere in the window."""
        def on_wheel(event):
            self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
            return "break"

        self.bind_all("<MouseWheel>", on_wheel)

        # A readonly Combobox changes its value on the wheel, so scrolling past
        # one would silently re-pick a glyph or preset. A binding on the widget
        # itself runs before the class binding, and "break" stops it there.
        def disarm(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Combobox):
                    child.bind("<MouseWheel>", on_wheel)
                disarm(child)

        disarm(self)

    def _fit_to_screen(self):
        """Shrink-wrap the window, capped to what the screen can show."""
        self.update_idletasks()
        head = self.preview.winfo_reqheight() + 20
        want = self._inner.winfo_reqheight()
        avail = self.winfo_screenheight() - head - 120
        self.canvas.configure(width=self._inner.winfo_reqwidth(),
                              height=max(200, min(want, avail)))
        self.update_idletasks()
        self.geometry("")

    # -------------------------------------------------------------- preview --

    def _ramp(self):
        return [palette.from_hex(c)
                for c in self.palettes["presets"][self.preset_var.get()]["ramp"]]

    def _render_clock(self, ramp):
        """The four digits laid out exactly as the skin lays them out."""
        text = (self.time_var.get().strip() or "00:00").replace(":", "")
        text = "".join(ch for ch in text if ch.isdigit())[:4].rjust(4, "0")

        dw = 80
        trim_one = self.vars["TrimOne"].get()
        track = self.vars["Tracking"].get()
        sep = self.vars["SepWidth"].get()

        # The time a minute on: every position whose digit differs sweeps.
        m = (int(text[:2]) * 60 + int(text[2:]) + 1) % (24 * 60)
        nxt = f"{m // 60:02d}{m % 60:02d}"

        trims = [trim_one if ch == "1" else 0 for ch in text]
        canvas_w = 4 * dw + 3 * track + sep
        img = Image.new("RGBA", (canvas_w, dw), (0, 0, 0, 0))

        x = sum(trims) - trims[0]
        for i, ch in enumerate(text):
            if i:
                gap = track - trims[i - 1] - trims[i]
                x += dw + gap + (sep if i == 2 else 0)
            glyph = self._glyph(ch, nxt[i])
            if ramp != self.digit_ramp:
                glyph = self._recolour(glyph, ramp)
            img.alpha_composite(glyph, (x, 0))
        return img

    def _glyph(self, ch, nxt):
        """What the skin would draw here -- a sweep frame if ch is changing."""
        frame = self.frame_var.get()
        if ch != nxt and self.morph_var.get() and frame:
            p = (palette.DIGITS_DIR / "morph" / self.cell_var.get()
                 / f"{ch}_{nxt}" / f"{frame}.png")
            if p.exists():
                return Image.open(p).convert("RGBA")
        return self.digits[ch]

    def _font(self, size):
        if getattr(self, "_font_size", None) != size:
            self._font_size = size
            self._font_obj = ImageFont.truetype(FONT_PATH, size)
        return self._font_obj

    def _render_date(self, colour):
        text = datetime.now().strftime("%d %m %Y")
        font = self._font(max(6, self.vars["DateSize"].get()))
        x0, y0, x1, y1 = font.getbbox(text)
        img = Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((-x0, -y0), text, font=font,
                                 fill=(*tuple(colour), 255))
        return img

    def _render_block(self, ramp):
        """Digits plus the date line, laid out as the skin stacks them."""
        clock = self._render_clock(ramp)
        date = self._render_date(ramp[-1])
        gap = self.vars["DateGap"].get()
        w, h = clock.width, clock.height + gap + date.height
        block = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        block.alpha_composite(clock, (0, 0))
        block.alpha_composite(date, ((w - date.width) // 2, clock.height + gap))
        return block

    def _recolour(self, glyph, ramp):
        import numpy as np
        a = np.asarray(glyph)
        rgb, alpha = a[:, :, :3], a[:, :, 3]
        out = palette.remap_rgb(rgb, self.digit_ramp, ramp)
        out = np.where(alpha[:, :, None] > 0, out, 0).astype(np.uint8)
        return Image.fromarray(np.dstack([out, alpha]), "RGBA")

    def _refresh_preview(self):
        ramp = self._ramp()
        self._describe_pace()

        self.swatch.delete("all")
        for i, c in enumerate(ramp):
            self.swatch.create_rectangle(i * 30, 0, i * 30 + 29, 22,
                                         fill=palette.to_hex(c), outline="")

        import numpy as np
        # With "Wallpaper" unticked the background keeps whatever preset is
        # already applied, so the preview shows the mismatch you would get.
        bg_ramp = ramp if self.to_wall.get() else [
            palette.from_hex(c)
            for c in self.palettes["presets"][self.config_data["preset"]]["ramp"]
        ]
        arr = palette.remap_rgb(np.asarray(self.wall_crop), self.wall_ramp,
                                bg_ramp)
        scene = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")

        block = self._render_block(ramp if self.to_dig.get() else self.digit_ramp)
        s = CLOCK_BOX[2] / block.width
        block = block.resize((CLOCK_BOX[2], max(1, round(block.height * s))),
                             Image.LANCZOS)
        scene.alpha_composite(block, (CLOCK_BOX[0] - CROP[0],
                                      CLOCK_BOX[1] - CROP[1]))

        w = round(scene.width * PREVIEW_SCALE)
        h = round(scene.height * PREVIEW_SCALE)
        self._photo = ImageTk.PhotoImage(scene.resize((w, h), Image.LANCZOS))
        self.preview.configure(image=self._photo)

    # -------------------------------------------------------------- actions --

    def _on_slide(self, name, value):
        new = int(round(float(value)))
        if new == self.vars[name].get():
            return
        self.vars[name].set(new)
        skin.set_var(name, new)
        self._refresh_preview()

    def _on_scrub(self, value):
        new = int(round(float(value)))
        if new != self.frame_var.get():
            self.frame_var.set(new)
            self._refresh_preview()

    def _set_time(self, t):
        self.time_var.set(t)
        self._refresh_preview()

    def _now(self):
        self._set_time(datetime.now().strftime("%H:%M"))

    def _toggle_debug(self):
        cur = skin.read_vars().get("Debug", "0")
        skin.set_var("Debug", 0 if cur.strip() == "1" else 1)
        skin.bang("!UpdateMeasure", "mMinutes")
        self.status.set("toggled skin debug clock (runtime only)")

    def _refresh_skin(self):
        skin.refresh()
        self.status.set("skin refreshed")

    def _on_glyph(self, digit):
        cfg = palette.load_config()
        cfg.setdefault("glyphs", {})[digit] = self.glyph_vars[digit].get()
        palette.save_config(cfg)
        self.config_data = cfg
        # Fill the preview with the digit being changed so it can be judged
        # without hunting for a time that happens to contain it.
        self.time_var.set(digit * 4)
        self._run(f"rebuilding digit {digit}", self._rebuild_worker)

    def _rebuild(self):
        self._run("rebuilding digits from source art", self._rebuild_worker)

    def _rebuild_worker(self):
        extract_digits.main()
        # main() rebuilds in the sheet's own palette, so without this the
        # digits would snap back to violet under any other preset.
        cfg = palette.load_config()
        palette.apply_preset(cfg["preset"], wallpaper=False, digits=True,
                             set_desktop=False, remember=False)
        skin.refresh()
        return f"digits rebuilt, {cfg['preset']} re-applied, skin refreshed"

    def _apply(self):
        self._run("applying preset -- rebuilding wallpaper", self._apply_worker)

    def _apply_worker(self):
        values = {n: v.get() for n, v in self.vars.items()}
        values["Morph"] = 1 if self.morph_var.get() else 0
        values["MorphCell"] = self.cell_var.get()
        skin.write_vars(values)
        # Not a [Variables] entry and not reachable by !SetVariable, so it can
        # only take effect on the refresh below.
        skin.write_update_ms(self.rate_var.get())

        rep = palette.apply_preset(
            self.preset_var.get(),
            wallpaper=self.to_wall.get(),
            digits=self.to_dig.get(),
            resample=self.resample_var.get(),
        )
        skin.refresh()
        bits = [f"preset {rep['preset']}"]
        if "wallpaper" in rep:
            bits.append(f"wallpaper {rep['wallpaper']['colours']} colours")
        if "digits" in rep:
            bits.append(f"{rep['digits']} digit files")
        return ", ".join(bits) + ", skin refreshed"

    # ----------------------------------------------------------- threading --

    def _run(self, message, worker):
        if self._busy:
            return
        self._busy = True
        self.status.set(message + "...")

        def go():
            try:
                self.jobs.put(("ok", worker()))
            except Exception as exc:                      # noqa: BLE001
                self.jobs.put(("err", exc))

        threading.Thread(target=go, daemon=True).start()

    def _drain(self):
        try:
            kind, payload = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            if kind == "ok":
                self.status.set(payload)
                self.config_data = palette.load_config()
                self._scan_glyphs()
                for d, box in self.glyph_boxes.items():
                    # picks up PNGs dropped into source_digits/ while running
                    box["values"] = self.glyph_options[d]
                    self.glyph_vars[d].set(self.glyph_choice[d])
                self._reload_digits()
                self._refresh_preview()
            else:
                self.status.set(f"failed: {payload}")
                messagebox.showerror("Failed", str(payload))
        self.after(100, self._drain)


if __name__ == "__main__":
    App().mainloop()
