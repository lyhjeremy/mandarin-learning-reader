#!/usr/bin/env python3
"""
mandarin_learner_gui.py — Tkinter GUI for the Mandarin Learning Reader
======================================================================
A karaoke-style read-along that teaches Mandarin through listening, speaking
and pronunciation feedback. Built on mandarin_learner_engine.py.

Layout
------
    ┌───────────┬───────────────────────────────────────────────┐
    │ chapter   │  previous sentence            (small, dim)     │
    │ list      │                                                │
    │ (sidebar) │  ▓ CURRENT SENTENCE ▓  ← big, pinyin per char  │
    │           │      coloured green/red after grading          │
    │           │                                                │
    │           │  next sentence(s)             (small, dim)     │
    ├───────────┴───────────────────────────────────────────────┤
    │  ▶ Play  ● Record  ↻ Replay  ⏭ Next   voice▾ speed▾ S/T▾   │
    │  progress ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬  accuracy: 92%   status…        │
    └────────────────────────────────────────────────────────────┘

Concurrency: every blocking job (book load, synthesis, mic record, Whisper
transcription, script conversion) runs on a daemon thread that pushes a
message onto a queue.Queue; a root.after(100, poll) loop drains it on the Tk
main thread. Tk is NEVER touched from a worker thread.

Run:  python3 mandarin_learner_gui.py
"""

import os
import queue
import threading
import tempfile
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import mandarin_learner_engine as E


# ── CJK-capable fonts per platform ────────────────────────────────────────────

def _pick_cjk_font():
    import sys
    if sys.platform == "darwin":
        return "PingFang SC"
    if os.name == "nt":
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"


CJK_FONT = _pick_cjk_font()

# ── Colour themes ─────────────────────────────────────────────────────────────

THEMES = {
    "light": {
        "bg": "#f6f6f4", "panel": "#ffffff", "fg": "#1c1c1e", "dim": "#9a9a9e",
        "accent": "#2f6fed", "sel": "#e4ecff", "border": "#dcdce0",
        "green": "#1f9d3b", "red": "#d13b3b", "amber": "#d99310",
        "pinyin": "#6a6a70", "cur_bg": "#eef3ff",
    },
    "dark": {
        "bg": "#1e1e22", "panel": "#2a2a30", "fg": "#ececf0", "dim": "#6f6f78",
        "accent": "#5b8cff", "sel": "#33405e", "border": "#3a3a42",
        "green": "#4bd06a", "red": "#ff6b6b", "amber": "#ffb300",
        "pinyin": "#a6a6b0", "cur_bg": "#26304a",
    },
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Mandarin Learning Reader")
        self.root.geometry("1120x760")
        self.root.minsize(880, 560)

        self.q = queue.Queue()

        # ── State ────────────────────────────────────────────────────────────
        prog = E.load_progress()
        s = prog["settings"]
        self.settings = s
        self.theme_name = s.get("theme", "light")

        self.book = None
        self.book_path = None
        self.book_title = ""
        self.chapters = []                # engine chapter dicts
        self.chapter_idx = None           # 0-based index into self.chapters
        self.units = []                   # [{text, chars}] for current chapter
        self.unit_idx = 0                 # current sentence within chapter
        self.detected_script = "unknown"
        self.grading = False
        self.recorder = None
        self.recording = False
        self._rec_seq = 0                 # unique temp-WAV counter per recording

        # Tk variables bound to controls.
        self.voice_var = tk.StringVar(value=self._voice_label(s.get("voice")))
        self.speed_var = tk.StringVar(value=self._speed_label(s.get("speed", 1.0)))
        self.script_var = tk.StringVar(value=self._script_label(s.get("script_mode", "none")))
        self.status_var = tk.StringVar(value="Open a Chinese .epub to begin.")
        self.accuracy_var = tk.StringVar(value="")
        self.autoadv_var = tk.BooleanVar(value=s.get("auto_advance", True))
        self.lenient_var = tk.BooleanVar(value=s.get("pinyin_tolerance", False))

        # Whisper engine (loaded lazily on first Record).
        self.whisper = E.WhisperEngine(s.get("whisper_model", E.WHISPER_MODEL_DEFAULT))
        self._whisper_ready = False

        self._init_audio()
        self._build_ui()
        self._apply_theme()
        self._bind_keys()

        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Offer to resume the last book.
        last = prog.get("last_book")
        if last and Path(last).exists():
            self.root.after(200, lambda: self._open_book(last, resume=True))

    # ── Audio init (pygame mixer) ─────────────────────────────────────────────
    def _init_audio(self):
        self.have_mixer = False
        try:
            import pygame
            pygame.mixer.init()
            self.pygame = pygame
            self.have_mixer = True
        except Exception:
            self.pygame = None

    # ── Small label helpers ───────────────────────────────────────────────────
    def _voice_label(self, vid):
        for label, v in E.VOICES.items():
            if v == vid:
                return label
        return next(iter(E.VOICES))

    def _script_label(self, mode):
        return {"none": "Original script", "s2t": "→ Traditional",
                "t2s": "→ Simplified"}.get(mode, "Original script")

    def _script_mode(self):
        return {"Original script": "none", "→ Traditional": "s2t",
                "→ Simplified": "t2s"}.get(self.script_var.get(), "none")

    def _voice_id(self):
        return E.VOICES.get(self.voice_var.get(), E.DEFAULT_VOICE)

    def _speed_label(self, speed):
        return f"{float(speed):g}×"

    def _speed(self):
        """Parse the speed picker back to a float tier, robustly."""
        raw = self.speed_var.get().rstrip("×x").strip()
        try:
            val = float(raw)
        except ValueError:
            return 1.0
        # Snap to the nearest defined tier so it always maps to a rate.
        return min(E.SPEEDS, key=lambda s: abs(s - val))

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        # Top bar: open button + book title + theme toggle.
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(side="top", fill="x")
        ttk.Button(top, text="📖  Open EPUB…", command=self._choose_book).pack(side="left")
        self.title_lbl = ttk.Label(top, text="No book open", font=(CJK_FONT, 13, "bold"))
        self.title_lbl.pack(side="left", padx=14)
        self.theme_btn = ttk.Button(top, text="🌙", width=3, command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        self.script_info = ttk.Label(top, text="", font=(CJK_FONT, 10))
        self.script_info.pack(side="right", padx=10)

        # Main split: chapter sidebar | reader.
        main = ttk.Frame(self.root)
        main.pack(side="top", fill="both", expand=True)

        side = ttk.Frame(main, width=250)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="Chapters", padding=(10, 6),
                  font=(CJK_FONT, 11, "bold")).pack(side="top", anchor="w")
        self.chapter_list = tk.Listbox(side, activestyle="none", exportselection=False,
                                       highlightthickness=0, borderwidth=0,
                                       font=(CJK_FONT, 11))
        self.chapter_list.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        cs = ttk.Scrollbar(side, orient="vertical", command=self.chapter_list.yview)
        cs.pack(side="right", fill="y")
        self.chapter_list.config(yscrollcommand=cs.set)
        self.chapter_list.bind("<<ListboxSelect>>", self._on_chapter_pick)

        # Reader: previous / current / next, all centered.
        self.reader = tk.Frame(main)
        self.reader.pack(side="left", fill="both", expand=True)

        self.prev_lbl = tk.Label(self.reader, text="", justify="center",
                                 font=(CJK_FONT, 16), wraplength=680)
        self.prev_lbl.pack(side="top", pady=(28, 6))

        # The current sentence lives on a Canvas so each character can be a
        # vertical pinyin+char stack, individually colourable.
        self.cur_canvas = tk.Canvas(self.reader, height=180, highlightthickness=0)
        self.cur_canvas.pack(side="top", fill="x", expand=False, padx=20, pady=8)
        self.cur_canvas.bind("<Configure>", lambda e: self._render_current())

        self.next_lbl = tk.Label(self.reader, text="", justify="center",
                                 font=(CJK_FONT, 16), wraplength=680)
        self.next_lbl.pack(side="top", pady=(6, 10))

        self.heard_lbl = tk.Label(self.reader, text="", justify="center",
                                  font=(CJK_FONT, 13))
        self.heard_lbl.pack(side="top", pady=(0, 4))

        # Transport bar.
        bar = ttk.Frame(self.root, padding=(12, 8))
        bar.pack(side="bottom", fill="x")

        self.play_btn = ttk.Button(bar, text="▶  Play", command=self.on_play, width=9)
        self.play_btn.pack(side="left", padx=3)
        self.record_btn = ttk.Button(bar, text="●  Record", command=self.on_record, width=11)
        self.record_btn.pack(side="left", padx=3)
        self.replay_btn = ttk.Button(bar, text="↻  Replay", command=self.on_play, width=9)
        self.replay_btn.pack(side="left", padx=3)
        self.prev_btn = ttk.Button(bar, text="⏮", command=self.on_prev, width=3)
        self.prev_btn.pack(side="left", padx=3)
        self.next_btn = ttk.Button(bar, text="Next  ⏭", command=self.on_next, width=9)
        self.next_btn.pack(side="left", padx=3)
        self.stop_btn = ttk.Button(bar, text="⏹  Stop", command=self.on_stop, width=8)
        self.stop_btn.pack(side="left", padx=3)

        # Labelled pickers on the right, reading left-to-right.
        controls = ttk.Frame(bar)
        controls.pack(side="right")
        ttk.Label(controls, text="Voice").pack(side="left", padx=(0, 3))
        ttk.OptionMenu(controls, self.voice_var, self.voice_var.get(),
                       *E.VOICES.keys(),
                       command=lambda *_: self._save_settings()).pack(side="left", padx=(0, 12))
        ttk.Label(controls, text="Speed").pack(side="left", padx=(0, 3))
        ttk.OptionMenu(controls, self.speed_var, self.speed_var.get(),
                       *[self._speed_label(s) for s in E.SPEEDS],
                       command=lambda *_: self._save_settings()).pack(side="left", padx=(0, 12))
        ttk.Label(controls, text="Script").pack(side="left", padx=(0, 3))
        ttk.OptionMenu(controls, self.script_var, self.script_var.get(),
                       "Original script", "→ Traditional", "→ Simplified",
                       command=lambda *_: self._on_script_change()).pack(side="left")

        # Progress + accuracy + status line.
        statusrow = ttk.Frame(self.root, padding=(12, 0))
        statusrow.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(statusrow, mode="determinate", length=240)
        self.progress.pack(side="left", padx=(0, 10), pady=4)
        self.pos_lbl = ttk.Label(statusrow, text="—", font=(CJK_FONT, 10))
        self.pos_lbl.pack(side="left")
        self.autoadv_chk = ttk.Checkbutton(
            statusrow, text="Auto-advance ≥90%", variable=self.autoadv_var,
            command=self._save_settings)
        self.autoadv_chk.pack(side="left", padx=(16, 0))
        self.lenient_chk = ttk.Checkbutton(
            statusrow, text="Lenient (accept right sound)", variable=self.lenient_var,
            command=self._save_settings)
        self.lenient_chk.pack(side="left", padx=(10, 0))
        self.acc_lbl = ttk.Label(statusrow, textvariable=self.accuracy_var,
                                 font=(CJK_FONT, 12, "bold"))
        self.acc_lbl.pack(side="right")
        ttk.Label(statusrow, textvariable=self.status_var,
                  font=(CJK_FONT, 10)).pack(side="right", padx=12)

        if not E.HAVE_WHISPER or not E.HAVE_SOUNDDEVICE:
            miss = []
            if not E.HAVE_SOUNDDEVICE:
                miss.append("sounddevice (mic)")
            if not E.HAVE_WHISPER:
                miss.append("faster-whisper (speech grading)")
            self.status_var.set("Recording disabled — missing: " + ", ".join(miss))
            self.record_btn.state(["disabled"])

    # ── Theming ───────────────────────────────────────────────────────────────
    def _apply_theme(self):
        t = THEMES[self.theme_name]
        self.root.configure(bg=t["bg"])
        for f in (self.reader,):
            f.configure(bg=t["bg"])
        self.cur_canvas.configure(bg=t["cur_bg"])
        self.prev_lbl.configure(bg=t["bg"], fg=t["dim"])
        self.next_lbl.configure(bg=t["bg"], fg=t["dim"])
        self.heard_lbl.configure(bg=t["bg"], fg=t["dim"])
        self.chapter_list.configure(bg=t["panel"], fg=t["fg"],
                                    selectbackground=t["sel"], selectforeground=t["fg"])
        self.style.configure("TFrame", background=t["bg"])
        self.style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        self.style.configure("TButton", font=(CJK_FONT, 11),
                             background=t["panel"], foreground=t["fg"])
        self.style.map("TButton",
                       background=[("active", t["sel"]), ("disabled", t["bg"])],
                       foreground=[("disabled", t["dim"])])
        self.style.configure("TMenubutton", background=t["panel"], foreground=t["fg"])
        self.style.map("TMenubutton", background=[("active", t["sel"])])
        self.style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        self.style.map("TCheckbutton", background=[("active", t["bg"])],
                       foreground=[("active", t["fg"])])
        self.style.configure("Horizontal.TProgressbar",
                             background=t["accent"], troughcolor=t["panel"])
        self.theme_btn.configure(text="☀️" if self.theme_name == "dark" else "🌙")
        # Reset the accuracy readout colour unless a grade is currently shown.
        if not self.accuracy_var.get():
            self.acc_lbl.configure(foreground=t["fg"])
        self._render_current()

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.settings["theme"] = self.theme_name
        self._apply_theme()
        self._save_settings()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────
    def _bind_keys(self):
        self.root.bind("<space>", lambda e: self._key_guard(e, self.on_play))
        self.root.bind("r", lambda e: self._key_guard(e, self.on_record))
        self.root.bind("R", lambda e: self._key_guard(e, self.on_record))
        self.root.bind("<Right>", lambda e: self._key_guard(e, self.on_next))
        self.root.bind("<Left>", lambda e: self._key_guard(e, self.on_prev))
        self.root.bind("<Return>", lambda e: self._key_guard(e, self.on_play))
        self.root.bind("<Escape>", lambda e: self._key_guard(e, self.on_stop))

    def _key_guard(self, event, fn):
        # Don't hijack typing inside entry/listbox widgets.
        w = self.root.focus_get()
        if isinstance(w, (tk.Entry, ttk.Entry)):
            return
        fn()
        return "break"

    # ── Book opening ──────────────────────────────────────────────────────────
    def _choose_book(self):
        path = filedialog.askopenfilename(
            title="Open a Chinese EPUB",
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self._open_book(path)

    def _open_book(self, path, resume=False):
        self.status_var.set("Loading book…")
        self._busy(True)
        threading.Thread(target=self._load_book_worker, args=(path, resume),
                         daemon=True).start()

    def _load_book_worker(self, path, resume):
        try:
            book, title, author = E.load_book(path)
            chapters = E.gather_chapters(book)
            sample = "".join(c["text"] for c in chapters if not c["junk"])[:5000]
            script = E.detect_script(sample)
            self.q.put(("book_loaded", (path, title, author, chapters, script, resume)))
        except Exception as exc:
            self.q.put(("error", f"Could not open book:\n{exc}"))

    def _on_book_loaded(self, payload):
        path, title, author, chapters, script, resume = payload
        self.book_path = path
        self.book_title = title
        self.chapters = chapters
        self.detected_script = script
        self.title_lbl.config(text=title)
        self.script_info.config(text=f"Detected: {script}")
        self._busy(False)

        self.chapter_list.delete(0, tk.END)
        for c in chapters:
            flag = "  ⚠" if c["junk"] else ""
            self.chapter_list.insert(tk.END, f"{c['index']:>3}  {c['title'][:26]}{flag}")

        # Resume to saved position, else first non-junk chapter.
        start_ch, start_unit = 0, 0
        if resume:
            pos = E.get_position(path)
            for i, c in enumerate(chapters):
                if c["index"] == pos["chapter_index"]:
                    start_ch = i
                    start_unit = pos["sentence_index"]
                    break
        else:
            for i, c in enumerate(chapters):
                if not c["junk"]:
                    start_ch = i
                    break
        self._load_chapter(start_ch, start_unit)
        self.status_var.set("Ready.  Space = Play · R = Record · → = Next")

    # ── Chapter / unit navigation ─────────────────────────────────────────────
    def _on_chapter_pick(self, _evt):
        sel = self.chapter_list.curselection()
        if sel:
            self._load_chapter(sel[0], 0)

    def _load_chapter(self, ch_index, unit_index=0):
        if not self.chapters:
            return
        ch_index = max(0, min(ch_index, len(self.chapters) - 1))
        self.chapter_idx = ch_index
        chapter = self.chapters[ch_index]
        text = E.convert_script(chapter["text"], self._script_mode())
        self.units = E.chunk_text_for_learning(text)
        self.unit_idx = max(0, min(unit_index, len(self.units) - 1)) if self.units else 0

        self.chapter_list.selection_clear(0, tk.END)
        self.chapter_list.selection_set(ch_index)
        self.chapter_list.see(ch_index)
        self._show_unit(reset_marks=True)
        self._save_position()
        # Pre-cache a few upcoming sentences in the background.
        self._prefetch()

    def _show_unit(self, reset_marks=True):
        if not self.units:
            self.prev_lbl.config(text="")
            self.next_lbl.config(text="")
            self.cur_canvas.delete("all")
            return
        i = self.unit_idx
        self.prev_lbl.config(text=self.units[i - 1]["text"] if i > 0 else "")
        nxt = ""
        if i + 1 < len(self.units):
            nxt = self.units[i + 1]["text"]
            if i + 2 < len(self.units):
                nxt += "\n" + self.units[i + 2]["text"]
        self.next_lbl.config(text=nxt)
        if reset_marks:
            self.current_marks = None
            self.accuracy_var.set("")
            self.heard_lbl.config(text="")
        self._render_current()

        total = len(self.units)
        self.progress.config(maximum=total, value=i + 1)
        self.pos_lbl.config(text=f"Sentence {i + 1} / {total}   "
                                 f"(Ch {self.chapters[self.chapter_idx]['index']})")

    def _render_current(self):
        """Draw the current sentence as per-character pinyin+char stacks."""
        if not getattr(self, "cur_canvas", None):
            return
        self.cur_canvas.delete("all")
        if not self.units:
            return
        t = THEMES[self.theme_name]
        cells = E.annotate(self.units[self.unit_idx]["text"])
        marks = getattr(self, "current_marks", None)

        char_font = (CJK_FONT, 40)
        py_font = (CJK_FONT, 15)
        pad_x = 6
        cell_w_default = 62
        line_h = 96

        cw = self.cur_canvas.winfo_width() or 800
        # Layout left-to-right with wrapping.
        # First measure each cell width.
        import tkinter.font as tkfont
        cf = tkfont.Font(family=CJK_FONT, size=40)
        pf = tkfont.Font(family=CJK_FONT, size=15)

        # Break cells into lines that fit the canvas width.
        lines = [[]]
        line_w = 0
        for idx, cell in enumerate(cells):
            wch = max(cf.measure(cell["char"]), pf.measure(cell["py"])) + pad_x * 2
            wch = max(wch, 26)
            if line_w + wch > cw - 20 and lines[-1]:
                lines.append([])
                line_w = 0
            lines[-1].append((idx, cell, wch))
            line_w += wch

        total_h = max(line_h * len(lines), line_h)
        self.cur_canvas.config(height=total_h + 10)
        y0 = 8
        # Map cell idx -> mark colour.
        def colour_for(idx, cell):
            if not cell["py"]:            # punctuation / space
                return t["fg"]
            if marks is None:
                return t["fg"]
            # marks index counts only CJK ideographs
            cjk_index = sum(1 for c in cells[:idx] if c["py"])
            if cjk_index < len(marks):
                m = marks[cjk_index]
                if m["ok"]:
                    return t["green"]
                if m.get("sound"):
                    return t["amber"]
                return t["red"]
            return t["fg"]

        for li, line in enumerate(lines):
            lw = sum(w for _, _, w in line)
            x = (cw - lw) / 2
            ytop = y0 + li * line_h
            for idx, cell, wch in line:
                cx = x + wch / 2
                col = colour_for(idx, cell)
                if cell["py"]:
                    self.cur_canvas.create_text(cx, ytop + 14, text=cell["py"],
                                                fill=t["pinyin"], font=py_font)
                self.cur_canvas.create_text(cx, ytop + 56, text=cell["char"],
                                            fill=col, font=char_font)
                x += wch

    # ── Transport actions ─────────────────────────────────────────────────────
    def on_play(self):
        if not self.units:
            return
        text = self.units[self.unit_idx]["text"]
        voice = self._voice_id()
        speed = self._speed()
        self.status_var.set("Synthesising…" if not
                            E.is_synth_cached(text, voice, speed) else "Playing…")
        threading.Thread(target=self._play_worker, args=(text, voice, speed),
                         daemon=True).start()

    def _play_worker(self, text, voice, speed):
        try:
            path = E.synth_sentence(text, voice, speed)
            self.q.put(("play_file", path))
        except Exception as exc:
            self.q.put(("error", f"Synthesis failed:\n{exc}"))

    def _do_play_file(self, path):
        if not self.have_mixer:
            self.status_var.set("Audio playback unavailable (pygame mixer).")
            return
        try:
            self.pygame.mixer.music.load(path)
            self.pygame.mixer.music.play()
            self.status_var.set("Playing…")
        except Exception as exc:
            self.status_var.set(f"Playback error: {exc}")

    def on_record(self):
        if not (E.HAVE_SOUNDDEVICE and E.HAVE_WHISPER):
            return
        if self.recording:
            self._stop_recording()
            return
        # Start recording (toggle mode).
        try:
            self.recorder = E.Recorder(max_seconds=30)
            self.recorder.start()
        except Exception as exc:
            self.status_var.set(f"Mic error: {exc}")
            return
        self.recording = True
        self.record_btn.config(text="■  Stop rec")
        self.status_var.set("Listening… (press Record/R again to stop)")

    def _stop_recording(self):
        self.recording = False
        self.record_btn.config(text="●  Record")
        self.status_var.set("Transcribing…")
        self._rec_seq += 1                # unique path so a new take can't clobber
        wav = os.path.join(tempfile.gettempdir(),           # a WAV still being read
                           f"mandarin_rec_{os.getpid()}_{self._rec_seq}.wav")
        try:
            self.recorder.stop(wav)
        except Exception as exc:
            self.status_var.set(f"Recording error: {exc}")
            return
        text = self.units[self.unit_idx]["text"]
        threading.Thread(target=self._grade_worker, args=(wav, text),
                         daemon=True).start()

    def _grade_worker(self, wav, original):
        try:
            if not self._whisper_ready:
                self.q.put(("status", "Loading Whisper model (first run downloads)…"))
                self.whisper.load()
                self._whisper_ready = True
            heard = self.whisper.transcribe(wav)
            res = E.grade(original, heard,
                          pinyin_tolerance=self.settings.get("pinyin_tolerance", False),
                          tone_sensitive=self.settings.get("tone_sensitive", False))
            self.q.put(("graded", res))
        except Exception as exc:
            self.q.put(("error", f"Transcription failed:\n{exc}"))
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    def _on_graded(self, res):
        self.current_marks = res["marks"]
        pct = res["accuracy"] * 100
        self.accuracy_var.set(f"{pct:.0f}%")
        t = THEMES[self.theme_name]
        self.acc_lbl.config(foreground=t["green"] if res["auto_advance"] else t["red"])
        self.heard_lbl.config(text=f"heard: {res['heard']}")
        self._render_current()
        if res["auto_advance"] and self.settings.get("auto_advance", True):
            self.status_var.set(f"✓ {pct:.0f}% — advancing")
            self.root.after(900, self.on_next)
        else:
            self.status_var.set(f"{pct:.0f}% — Replay, Record again, or Next")

    def on_next(self):
        if not self.units:
            return
        if self.unit_idx + 1 < len(self.units):
            self.unit_idx += 1
            self._show_unit(reset_marks=True)
            self._save_position()
            self._prefetch()
        elif self.chapter_idx is not None and self.chapter_idx + 1 < len(self.chapters):
            self._load_chapter(self.chapter_idx + 1, 0)

    def on_prev(self):
        if not self.units:
            return
        if self.unit_idx > 0:
            self.unit_idx -= 1
            self._show_unit(reset_marks=True)
            self._save_position()
        elif self.chapter_idx is not None and self.chapter_idx > 0:
            self._load_chapter(self.chapter_idx - 1, 0)
            self.unit_idx = len(self.units) - 1
            self._show_unit(reset_marks=True)

    def on_stop(self):
        if self.have_mixer:
            try:
                self.pygame.mixer.music.stop()
            except Exception:
                pass
        if self.recording:
            self.recording = False
            self.record_btn.config(text="●  Record")
            try:
                self.recorder.stop(os.path.join(tempfile.gettempdir(), "_discard.wav"))
            except Exception:
                pass
        self.status_var.set("Stopped.")

    # ── Script / settings changes ─────────────────────────────────────────────
    def _on_script_change(self):
        self._save_settings()
        if self.chapter_idx is not None:
            self._load_chapter(self.chapter_idx, self.unit_idx)

    def _prefetch(self):
        """Warm the audio cache for the next few sentences in the background."""
        voice = self._voice_id()
        speed = self._speed()
        upcoming = self.units[self.unit_idx: self.unit_idx + 4]
        texts = [u["text"] for u in upcoming]
        threading.Thread(target=self._prefetch_worker, args=(texts, voice, speed),
                         daemon=True).start()

    def _prefetch_worker(self, texts, voice, speed):
        for t in texts:
            try:
                if not E.is_synth_cached(t, voice, speed):
                    E.synth_sentence(t, voice, speed)
            except Exception:
                pass

    def _save_settings(self):
        self.settings.update({
            "voice": self._voice_id(),
            "speed": self._speed(),
            "script_mode": self._script_mode(),
            "theme": self.theme_name,
            "auto_advance": bool(self.autoadv_var.get()),
            "pinyin_tolerance": bool(self.lenient_var.get()),
        })
        if self.book_path and self.chapter_idx is not None:
            self._save_position()               # writes settings + book position
        else:
            data = E.load_progress()             # persist settings with no book open
            data["settings"].update(self.settings)
            E.save_progress(data)

    def _save_position(self):
        if not self.book_path or self.chapter_idx is None:
            return
        try:
            E.record_position(
                self.book_path, self.book_title,
                self.chapters[self.chapter_idx]["index"],
                self.unit_idx, settings=self.settings)
        except Exception:
            pass

    # ── Busy / poll / close ───────────────────────────────────────────────────
    def _busy(self, on):
        state = ["disabled"] if on else ["!disabled"]
        for b in (self.play_btn, self.next_btn, self.prev_btn):
            try:
                b.state(state)
            except Exception:
                pass

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "book_loaded":
                    self._on_book_loaded(payload)
                elif kind == "play_file":
                    self._do_play_file(payload)
                elif kind == "graded":
                    self._on_graded(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "error":
                    self._busy(False)
                    self.status_var.set("Error.")
                    messagebox.showerror("Mandarin Learner", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_close(self):
        self._save_position()
        try:
            if self.have_mixer:
                self.pygame.mixer.music.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    missing = E.missing_deps()
    root = tk.Tk()
    if missing:
        messagebox.showerror(
            "Missing dependencies",
            "Install the core packages first:\n\n"
            "  pip install " + " ".join(missing))
        root.destroy()
        return
    try:
        App(root)
        root.mainloop()
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
