# Mandarin Learning Reader — Full Project Overview

> A hand-off / spec document for building a **Chinese EPUB → interactive Mandarin
> learning tool**. It reuses the proven architecture of the EPUB→Audiobook
> generator (engine / CLI / GUI split, edge-tts synthesis, atomic caching), but
> repurposes it into a **karaoke-style read-along that teaches Mandarin through
> listening, speaking, and pronunciation feedback**.
>
> Point a fresh Claude Code session at this file to bootstrap the build.
>
> **Author context:** builder speaks Cantonese natively, learning Mandarin.
> Comfortable reading Traditional, learning to read Simplified faster.

---

## ✅ Build status & handoff (updated 2026-07-01)

**Status: built, tested, and published.** Everything in the CORE spec below is
implemented and working; a few pieces were extended beyond the original spec
(see "Deviations"). This section is the current-state summary — the rest of the
document is the original design spec, kept for rationale.

**Where it lives**
- GitHub (public): https://github.com/lyhjeremy/mandarin-learning-reader
- Showcase (GitHub Pages): https://lyhjeremy.github.io/mandarin-learning-reader/
- The project folder itself is the git repo (branch `main`).

**What was built**
- `mandarin_learner_engine.py` — the engine; all CORE subsystems (§5.1–5.9), no UI.
- `mandarin_learner.py` — CLI / test harness (`--list --detect --chunks --pinyin
  --synth --grade --transcribe`).
- `mandarin_learner_gui.py` — **classic** Tkinter GUI (this spec's original layout).
- `mandarin_learner_gui_v2.py` — **modern** GUI (Spotify/Audible/karaoke look): a big
  centred karaoke stage, custom rounded transport buttons with the **keyboard key
  printed on each**, a circular Play, a progress scrubber + live score, dark by
  default with a light toggle.
- `README.md`, `index.html` (showcase), `requirements.txt`, `docs/` screenshots.

**How to run**
```bash
pip install -r requirements.txt
python3 mandarin_learner_gui_v2.py            # modern player (recommended)
python3 mandarin_learner_gui.py               # classic
python3 mandarin_learner.py book.epub --list  # CLI
```
Dependencies are installed in the local Python; the Whisper `small` model is already
downloaded (first Record works offline). edge-tts needs internet per *new* sentence,
then caches it.

**Deviations / extensions from the spec below**
- **Chunking also splits on commas & colons (，,：:)**, not just the strong stops in
  §5.2 — long comma-spliced sentences become short, speakable phrases; the packing
  step still re-stacks any that end up too short. (The spec originally excluded the
  comma; changed at the builder's request.)
- **Grading normalises script (§5.8):** faster-whisper often transcribes Mandarin as
  Traditional characters even for a Simplified book, so `grade()` compares both sides
  in a common script (学 == 學) while still displaying the original characters.
  Without this, correctly-read Simplified sentences scored ~70%.
- **Sound/pinyin tolerance (§5.8 OPTIONAL) is implemented** as a "Lenient" toggle
  (amber = right sound, wrong character), and **auto-advance is a UI toggle**.
- STT backend is **faster-whisper `small`** (openai-whisper wired as an automatic
  fallback); OpenCC via `opencc-python-reimplemented`.

**Notable fix:** the chapter list (a Tk Listbox) hijacked Space/Enter ("select
active row"), which snapped playback back to the chapter's first sentence — fixed in
**both** GUIs (guard re-selection + bind Space/Enter to Play on the listbox).

**Vs. the contingency tree (§9):** all CORE built at the full "aim-high" level; no
fallbacks were needed. The fallbacks remain available behind their seams.

---

## 0. How to read this document

This spec is organized so you can build **aggressively (aim high)** but always
have a safe path back. Every subsystem is tagged:

- **[CORE]** — must exist for the app to be useful. Build these first.
- **[OPTIONAL]** — enhances the experience; can ship later.
- **[FALLBACK]** — the simpler version to drop back to if a [CORE] item proves
  too hard, too slow, or too flaky.

The intended build order is **aim high on day one** (full speech-recognition
version), and if a piece doesn't work, consult its **[FALLBACK]** and downgrade
just that piece without touching the rest. See **Section 9 (Contingency Plans)**
for the full decision tree.

---

## 1. What this project is (in one paragraph)

A **desktop Python app that turns any Chinese `.epub` e-book into an interactive
Mandarin-learning environment.** It detects chapters, chunks the text into short
**sentence units** (respecting Chinese punctuation, packing very short adjacent
sentences together, no hard upper cap), and presents them **one at a time,
karaoke-style**, with **pinyin shown under every character**. The current
sentence is large and central; the previous and next sentences sit smaller
above/below for context. The user hears the sentence read aloud in a **Mandarin
edge-tts voice** (male or female, five speed tiers), reads along, then **speaks
it back**. The app **listens via a local, offline Whisper model**, compares the
user's speech to the original **character-by-character**, and paints each
character **green (correct) or red (wrong)**. If the user scores **≥ 90%**, the
app **auto-advances** to the next sentence; otherwise the user replays and
retries, or manually advances. Progress (which book, chapter, and sentence) is
**saved and restored** on reopen. An **optional Simplified ⇄ Traditional toggle**
lets the reader flip the script regardless of the EPUB's original form.

It ships as a **Tkinter GUI** and a **CLI**, both sharing one non-UI **engine**.

---

## 2. Relationship to the Audiobook Generator (what to reuse)

This project is a sibling of the EPUB→Audiobook app. **Reuse these wholesale**
(they are mature and format-agnostic):

- **EPUB parsing & text extraction** — `load_book()`, `extract_text()` (BeautifulSoup
  cleanup, NFKC normalize, junk stripping), `first_heading()`.
- **Chapter detection & junk flagging** — `gather_chapters()`, spine-in-reading-order
  walk, TOC title resolution, `JUNK_PATTERNS` (incl. Chinese 目录/版权/封面…).
- **CJK language logic** — `cjk_fraction()`, `is_cjk_text()`, CJK-aware sentence
  terminators, character counting.
- **edge-tts synthesis contract** — the **retry + atomic `.part`→rename + skip-existing**
  reliability pattern. Copy this wholesale for audio caching.
- **The 3-layer split** — engine / CLI / GUI, so front-ends never duplicate logic.
- **The Tkinter thread + `queue` + `after()` poll loop** — the correct concurrency
  pattern; all blocking work (synthesis, Whisper, book loading) runs on daemon
  threads that push messages to a queue drained on the main thread.
- **Settings/progress JSON dotfile in `$HOME`** — same persistence idea.

**What's NEW in this project (does not exist in the audiobook app):**

- Manual, sentence-stepped playback (not continuous read-along).
- Pinyin annotation under every character.
- Microphone capture + **local Whisper speech-to-text**.
- **Character-level pronunciation grading** and green/red feedback.
- **Auto-advance gated on ≥ 90% accuracy.**
- **Simplified ⇄ Traditional toggle** (OpenCC).

---

## 3. Architecture — the 3-layer split (keep this pattern)

```
┌──────────────────────────────┐     ┌──────────────────────────┐
│  mandarin_learner_gui.py     │     │   mandarin_learner.py    │
│  (Tkinter GUI)               │     │   (argparse CLI)         │
└──────────────┬───────────────┘     └────────────┬─────────────┘
               │        both import               │
               └───────────────┬──────────────────┘
                               ▼
            ┌──────────────────────────────────────────┐
            │       mandarin_learner_engine.py          │   ← THE CORE
            │  EPUB parse · text extract · chapter      │
            │  detect · CJK sentence chunking (smart    │
            │  packing) · pinyin annotation · script    │
            │  detect/convert (OpenCC) · edge-tts synth │
            │  (+retry/atomic/cache) · Whisper STT ·    │
            │  char-level grading · progress store      │
            └──────────────────────────────────────────┘
```

- **`mandarin_learner_engine.py`** — no UI imports, safe to import anywhere. All
  real work. Target ~600–800 lines.
- **`mandarin_learner_gui.py`** — Tkinter GUI: chapter list + reader + controls +
  mic feedback. Target ~1000–1300 lines.
- **`mandarin_learner.py`** — CLI front-end for headless use / testing (list
  chapters, dump chunked sentences, batch-synthesize audio, etc.).

**Why keep the split:** the CLI is invaluable for testing the engine (chunking,
pinyin, grading) without the GUI, and it keeps logic in exactly one place.

---

## 4. The user experience (what it feels like to use)

1. Open the app, pick a Chinese `.epub`. The app parses it, detects the script
   (Simplified/Traditional), lists chapters (junk-flagged ones marked).
2. Pick a chapter → the Reader opens at the last-saved sentence (or the start).
3. The **current sentence** is shown large, centered, with **pinyin under each
   character**. The **previous** sentence sits above and the **next** sentence(s)
   below, in a smaller, dimmer font.
4. User presses **Play** (or Spacebar) → the sentence is read aloud in Mandarin.
5. User presses **Record** (or a key) → speaks the sentence back.
6. Whisper transcribes; the engine grades character-by-character; each character
   turns **green or red**; an **accuracy %** shows.
7. If **≥ 90%**, the app **auto-advances**. Otherwise the user can **Replay**,
   **Record again**, or press **Next** to move on manually.
8. A **progress bar** shows position in the chapter/book. A **chapter list**
   (sidebar or top strip) allows jumping. **Stop** halts cleanly.
9. On close and reopen, the app **jumps straight back** to where the user was.

Controls, minimal and functional: **Play**, **Record**, **Replay/Repeat**,
**Next**, **Stop**, plus a **Simplified/Traditional toggle**, a **voice picker**
(male/female), and a **speed picker** (0.5 / 0.75 / 1.0 / 1.25 / 1.5).

---

## 5. Core subsystems (build order + reuse notes)

### 5.1 [CORE] EPUB parsing & text extraction — *reuse from audiobook app*
- `load_book(path) -> (book, title, author)` via **ebooklib** (`epub.read_epub`).
- `extract_text(html_bytes)` via **BeautifulSoup**: drop non-speakable tags,
  preserve paragraph breaks, `get_text()`, NFKC-normalize, strip soft hyphens /
  `�` / URLs / lone page numbers, collapse whitespace.
- `gather_chapters(book, min_chars) -> [{index, title, text, file_name, junk}]`,
  spine in reading order, TOC-title → first heading → "Section N".
- **Nothing here changes.** This is format-agnostic and already CJK-safe.

### 5.2 [CORE] CJK sentence chunking with smart packing — *new logic, adapt splitter*
The single most important **new** engine function. Rules agreed with the builder:

- **Respect punctuation first.** Split on sensible Chinese stops/pausers:
  `。 ！ ？ ； … 、` and the em-dash `—`. (Full stops, question/exclamation
  marks, semicolons, ellipses, enumeration comma, dash. Use common sense: these
  are natural "stop or pause" points.)
- **No hard upper cap.** If a single sentence is 18–20+ characters, keep it whole.
- **Pack short neighbors.** If a sentence is very short (≈ ≤ 5 characters) and the
  next one is also short, **merge them** so units aren't awkwardly tiny. Aim for a
  comfortable **~7–15 characters** per displayed unit, but never split a real
  sentence to hit that — packing only ever *joins*, never *cuts*.
- **Count by characters**, not words (CJK). Whitespace/punctuation don't count
  toward the "length" for packing decisions but stay in the displayed text.

```python
def chunk_text_for_learning(text, target_min=7, target_max=15):
    """
    Split Chinese text into learning units.

    Strategy:
      1. Split into raw sentences at CJK terminators (。！？；…、—).
      2. Walk sentences left-to-right, packing SHORT ones together:
         - if current unit's char_count < target_min, append next sentence
         - stop packing once >= target_min (or once appending would create an
           awkward join). NEVER split a sentence to fit target_max.
      3. Long single sentences (> target_max) pass through untouched.

    Returns: [ {text: str, chars: int}, ... ]

    Examples:
      "我很開心。你呢？"         -> [ {"我很開心。你呢？", 6} ]   # packed (both short)
      "學習很重要。這是一個關於中文學習的很長句子。"
                               -> [ {"學習很重要。", 5},
                                    {"這是一個關於中文學習的很長句子。", 15} ]
    """
```

- **CLI test hook:** `mandarin_learner.py --chunks book.epub --chapter 3` should
  print the chunked units so packing behavior is easy to eyeball.

### 5.3 [CORE] Pinyin annotation (per character) — *new*
- Use **`pypinyin`** (recommended, tone-mark support, better coverage) — or
  `xpinyin` as an alternative.
- Produce, for each character, its tone-marked pinyin. Skip whitespace and
  punctuation (they have no pinyin).

```python
from pypinyin import pinyin, Style

def annotate(text):
    """
    "我是學生" -> [
      {"char": "我", "py": "wǒ"},
      {"char": "是", "py": "shì"},
      {"char": "學", "py": "xué"},
      {"char": "生", "py": "shēng"},
    ]
    Punctuation/space chars come back with py="" (rendered without a pinyin cell).
    """
    out = []
    for ch in text:
        if ch.strip() == "" or not _is_cjk(ch):
            out.append({"char": ch, "py": ""})
        else:
            py = pinyin(ch, style=Style.TONE, heteronym=False)[0][0]
            out.append({"char": ch, "py": py})
    return out
```

- **Rendering:** each character is a small vertical stack — pinyin on top (or
  bottom), the big character below (or above). Lay them out left-to-right,
  wrapping as needed.
- **[OPTIONAL] Heteronyms (多音字):** pypinyin can't always pick the right reading
  in isolation. For MVP, take the default reading. If it matters later, pass more
  context (the whole sentence) to pypinyin so it can disambiguate.

### 5.4 [CORE, with OPTIONAL toggle] Script detection & Simplified ⇄ Traditional
- **Detect** the EPUB's script on load; label the book Simplified / Traditional /
  Mixed. A lightweight heuristic: convert a text sample S→T and T→S with OpenCC
  and compare which conversion changes fewer characters, or check for
  traditional-only characters (國學點體…).
- **Toggle [OPTIONAL but build it in]:** the builder wants to flip either
  direction (read a Simplified EPUB as Traditional and vice versa).

```python
from opencc import OpenCC
_cc = {"s2t": OpenCC("s2t"), "t2s": OpenCC("t2s")}

def convert_script(text, mode):   # mode in {"none", "s2t", "t2s"}
    return text if mode == "none" else _cc[mode].convert(text)
```

- **Where conversion happens:** convert the **display text** only. The pinyin and
  grading should run on whatever script is currently displayed, so the user reads
  and speaks in the form they chose.
- **[FALLBACK]** If OpenCC is slow, buggy, or produces odd output: **disable the
  toggle** and display the EPUB's original script as-is. Everything else still
  works. Keep the toggle behind a settings flag so it's a one-line disable.

### 5.5 [CORE] edge-tts Mandarin synthesis — *reuse retry/atomic/cache*
- Voices (offer **both**, user-selectable):
  - Female: `zh-CN-XiaoxiaoNeural` (very natural) — good default.
  - Male: `zh-CN-YunxiNeural` (natural male).
  - (Alternatives to expose if desired: `zh-CN-XiaoyiNeural`, `zh-CN-YunyangNeural`.)
- **Speed tiers (exactly five):** 0.5×, 0.75×, 1.0×, 1.25×, 1.5×. Map to edge-tts
  `rate` strings (e.g. `-50%`, `-25%`, `+0%`, `+25%`, `+50%`). This uses edge-tts's
  own rate — **no ffmpeg needed** for speed, which keeps dependencies light.
- **Synthesize per-sentence**, on demand, and **cache** by a key of
  `hash(text|voice|rate)` to a cache dir so replays are instant and re-opening a
  chapter is cheap.
- **Reuse the reliability contract from the audiobook engine:** up to 4 retries
  with backoff, treat 0-byte output as failure, write to a `.part` file then
  `os.replace()` to final. Skip synthesis if a non-empty cached file exists.

```python
CACHE_DIR = os.path.join(tempfile.gettempdir(), "mandarin_learner_tts_cache")
RATE_FOR_SPEED = {0.5:"-50%", 0.75:"-25%", 1.0:"+0%", 1.25:"+25%", 1.5:"+50%"}

def synth_sentence(text, voice, speed) -> str:
    """Return a path to a cached MP3, synthesizing (with retry+atomic) if absent."""
```

- **[FALLBACK]** If edge-tts rate at 0.5× sounds bad, add optional ffmpeg
  `atempo` rendering (as the audiobook app does) — but only if needed.

### 5.6 [CORE] Microphone capture — *new*
- Use **`sounddevice`** to record, **`scipy.io.wavfile`** (or `soundfile`) to
  write a temp WAV that Whisper can read (16 kHz mono is ideal for Whisper).
- Two capture modes to consider:
  - **Fixed window:** record N seconds on "Record" press (simple, predictable).
  - **[OPTIONAL] Push-to-talk:** hold a key to record, release to stop (nicer,
    slightly more code). Start with fixed-window or a Record→Stop toggle.
- Clean up temp WAVs after transcription.

```python
import sounddevice as sd, scipy.io.wavfile as wav, numpy as np
SR = 16000
def record_wav(path, seconds=8):
    audio = sd.rec(int(seconds*SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    wav.write(path, SR, audio)
```

### 5.7 [CORE] Local Whisper speech-to-text — *new, offline, no API*
- **Offline, no API key** (hard requirement). Two library choices:
  - **`faster-whisper`** (recommended): CTranslate2 backend, ~2–4× faster, lower
    RAM, easy CPU use. Model sizes: `tiny`, `base`, `small`, `medium`.
  - **`openai-whisper`** (reference implementation): simpler API, heavier.
- **Recommended default:** `faster-whisper` **`small`** for a good accuracy/speed
  balance on Mandarin; drop to **`base`** or **`tiny`** if latency is bad.
- **Load the model ONCE** at startup (or lazily on first use) and keep it in
  memory — reloading per sentence is far too slow.
- Force `language="zh"` and, if using faster-whisper, consider
  `initial_prompt=<original sentence>` is **NOT** advisable (it can bias the
  transcript toward "correct" and defeat grading) — transcribe **blind**.

```python
from faster_whisper import WhisperModel
_model = WhisperModel("small", device="cpu", compute_type="int8")  # load once

def transcribe(path):
    segments, _ = _model.transcribe(path, language="zh", beam_size=1)
    return "".join(seg.text for seg in segments).strip()
```

- **First-run note:** models download automatically (~40 MB tiny → ~460 MB small
  → ~1.5 GB medium). Tell the user this in the UI on first transcription.

### 5.8 [CORE] Character-level pronunciation grading — *new, the "green/red" logic*
Agreed approach: **character-by-character** comparison of the original text vs.
Whisper's transcript. But because Whisper may insert/delete characters, do a
proper **alignment** rather than a naive `zip`, so a single missed character
doesn't cascade into everything after it being marked wrong.

```python
import difflib, re

def _cjk_only(s):
    return [c for c in s if '\u4e00' <= c <= '\u9fff']

def grade(original_text, heard_text):
    """
    Align original vs. heard at the character level and mark each ORIGINAL
    character correct/incorrect.

    Returns:
      {
        "accuracy": 0.83,
        "auto_advance": False,        # True iff accuracy >= 0.90
        "marks": [ {"char":"我","ok":True}, {"char":"是","ok":True},
                   {"char":"學","ok":False}, {"char":"生","ok":True} ]
      }
    """
    orig = _cjk_only(original_text)
    heard = _cjk_only(heard_text)
    sm = difflib.SequenceMatcher(a=orig, b=heard, autojunk=False)
    ok = [False]*len(orig)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                ok[i] = True
    correct = sum(ok)
    total = len(orig) or 1
    acc = correct/total
    return {
        "accuracy": acc,
        "auto_advance": acc >= 0.90,
        "marks": [{"char": orig[i], "ok": ok[i]} for i in range(len(orig))],
    }
```

- **Green** = `ok True`, **Red** = `ok False`. Show `accuracy` as a percentage.
- **≥ 90% → auto-advance** to the next sentence. Otherwise stay put; user may
  Replay, Record again, or press Next.
- **[OPTIONAL — pinyin/phonetic tolerance]** Character-exact grading is strict:
  homophones and near-misses get marked wrong even if the *sound* was right. If
  this feels too punishing, add a **pinyin-level fallback**: when characters
  differ, compare their **pinyin** (optionally tone-insensitive) and count a
  "sound match" as correct (perhaps a different color, e.g. amber = right sound,
  wrong character). Build the strict version first; add tolerance only if the
  strict version frustrates real use.
- **[FALLBACK — no auto-advance]** If Whisper accuracy on the user's mic is too
  low to trust the 90% gate, **decouple grading from advancing**: still show
  green/red as feedback, but always require a manual **Next**. This preserves the
  learning value even if the auto-advance heuristic is unreliable.

### 5.9 [CORE] Progress persistence — *reuse dotfile idea*
- Save to `~/.mandarin_learner.json`. On launch, reopen the last book and jump to
  the saved chapter+sentence. Save on every advance and on clean exit.

```json
{
  "last_book": "/path/to/三體.epub",
  "settings": {
    "voice": "zh-CN-XiaoxiaoNeural",
    "speed": 1.0,
    "script_mode": "none",
    "whisper_model": "small"
  },
  "books": {
    "/path/to/三體.epub": {
      "title": "三體",
      "chapter_index": 2,
      "sentence_index": 5,
      "updated": "2026-07-01T10:30:00"
    }
  }
}
```

- **[FALLBACK]** If per-book position proves fiddly, at minimum remember the
  **last book + last chapter**; sentence-level resume is the nice-to-have.

---

## 6. The GUI — structure & mechanics

Single `App` class on a Tkinter root; `ttk` "clam" theme restyled. Reuse the
audiobook app's proven patterns.

- **Layout (minimal, functional):**
  - **Chapter list** — sidebar (left) or top strip; junk chapters visually
    flagged; click to jump. Shows a check/position marker for progress.
  - **Reader area (center):**
    - **Current sentence** — large, centered, each character stacked with its
      **pinyin**. After grading, characters are colored **green/red**.
    - **Previous sentence** (above) and **next sentence(s)** (below) — smaller,
      dimmer, for context.
  - **Transport / controls** — **Play**, **Record**, **Replay**, **Next**,
    **Stop**; **voice** (male/female), **speed** (5 tiers), **Simplified/
    Traditional** toggle. Place sensibly; exact arrangement is free.
  - **Progress bar** — position within chapter (and/or whole book).
  - **Accuracy readout** — shows last attempt's % after grading.
- **Threading model (reuse):** all blocking work — book load, TTS synth, mic
  record, Whisper transcribe, script conversion — runs on **daemon threads** that
  push messages onto a `queue.Queue`; a `root.after(100, self._poll)` loop drains
  it on the Tk main thread. **Never touch Tk from a worker thread.**
- **Playback:** **`pygame.mixer.music`** to play the cached sentence MP3
  (simple, cross-platform). Play is fire-and-forget per sentence (no seek bar
  needed in the minimal design).
- **Recording UX:** Record button toggles record→stop (or fixed 8 s). While
  transcribing, show a small "listening…/thinking…" state; results arrive via the
  queue and update the coloring.
- **Keyboard [OPTIONAL]:** Space = Play, `R` = Record, `→` = Next, `←` = Prev,
  `Enter` = Replay. Guard so typing in fields isn't hijacked.
- **Theming [OPTIONAL]:** light/dark palettes with a toggle, persisted (reuse the
  audiobook app's `THEMES` + `_reg()` registry pattern). Auto-pick a CJK-capable
  font (PingFang on macOS, Microsoft YaHei / PMingLiU on Windows, Noto Sans CJK
  on Linux).

---

## 7. The CLI — for testing & headless use

`mandarin_learner.py` (argparse). Suggested flags:

- `--list <book.epub>` — list chapters (with junk flags, char counts).
- `--chunks <book.epub> --chapter N` — print chunked learning units (verify 5.2).
- `--pinyin <book.epub> --chapter N` — print units with per-char pinyin.
- `--synth <book.epub> --chapter N --voice ... --speed ...` — pre-cache audio.
- `--convert {none,s2t,t2s}` — apply script conversion to output.
- `--grade "<original>" "<heard>"` — test the grading function directly.

The CLI is the fastest way to validate chunking, pinyin, conversion, and grading
without launching the GUI.

---

## 8. Dependencies

| Package | Tier | For |
|---|---|---|
| `edge-tts` | **CORE** | Mandarin TTS (free MS Edge voices, online) |
| `ebooklib` | **CORE** | Reading EPUB spine / TOC / metadata |
| `beautifulsoup4` | **CORE** | HTML → clean text extraction |
| `pypinyin` | **CORE** | Per-character pinyin (tone marks) |
| `faster-whisper` | **CORE** | Offline local speech-to-text (recommended) |
| `sounddevice` | **CORE** | Microphone capture |
| `scipy` *(or `soundfile`)* | **CORE** | Write WAV for Whisper |
| `pygame` | **CORE (GUI)** | Play sentence audio |
| `opencc-python-reimplemented` | OPTIONAL | Simplified ⇄ Traditional toggle |
| `openai-whisper` | ALT | Alternative STT (heavier than faster-whisper) |
| `ffmpeg` (system binary) | OPTIONAL | Only if edge-tts rate isn't enough for 0.5× |
| `tkinter` | GUI only | Bundled with most Python installs |

```bash
pip install edge-tts ebooklib beautifulsoup4 pypinyin faster-whisper sounddevice scipy pygame
pip install opencc-python-reimplemented        # optional: script toggle
```

- **Python 3.9+.** Needs **internet for edge-tts** each synthesis (audio is then
  cached). **Whisper runs fully offline** after the model file downloads once.
- **First run** downloads a Whisper model (~40 MB `tiny` → ~460 MB `small`). Warn
  the user in-UI.
- **Microphone permission** may need granting (macOS: System Settings → Privacy →
  Microphone for the terminal/app running Python).

---

## 9. Contingency plans (the decision tree)

Build the **aim-high** version first. If a specific piece fails, downgrade *only
that piece* using the matching fallback — the rest of the app is unaffected
because of the clean engine seams.

**A. Whisper too slow / too much RAM / laggy feedback**
1. Switch `faster-whisper` model **small → base → tiny**.
2. Ensure the model is **loaded once**, not per sentence.
3. Reduce recording length; use `beam_size=1`, `compute_type="int8"`.
4. **[FALLBACK]** If still unusable: ship **without live grading** — keep the
   karaoke read-along (play, read pinyin, manual Next). Add a "Record & check"
   button that's clearly optional/experimental. This is the V-simple version and
   is still a genuinely useful Mandarin reader.

**B. Grading feels too strict (homophones/near-misses marked red)**
1. Add **pinyin-level tolerance** (5.8 OPTIONAL): compare pinyin when characters
   differ; tone-insensitive if needed; show amber for "right sound, wrong char."
2. Lower the auto-advance gate (e.g. 80%) or make the threshold a setting.
3. **[FALLBACK]** Decouple grading from advancing: always manual **Next**, keep
   green/red purely as informational feedback.

**C. edge-tts flaky / rate-limited (it's an unofficial endpoint)**
1. Rely on the **retry + cache** contract (cached sentences never re-synth).
2. Pre-synthesize a chapter's sentences in the background after opening it.
3. **[FALLBACK]** Add a local TTS option (e.g. `pyttsx3`, or Piper zh voices) as
   a secondary provider behind the same `synth_sentence()` seam.

**D. 0.5× speed sounds bad via edge-tts rate**
1. **[FALLBACK]** Render slow speeds with ffmpeg `atempo` (reuse the audiobook
   app's cached speed-render), gated behind ffmpeg discovery. Only if needed.

**E. OpenCC toggle problematic**
1. **[FALLBACK]** Disable the toggle via a settings flag; display original script.

**F. pypinyin heteronyms wrong on some characters**
1. Pass **sentence context** to pypinyin for better disambiguation.
2. Accept minor errors for MVP; they don't block learning.

**G. Sentence-level resume fiddly**
1. **[FALLBACK]** Remember last book + last chapter only.

---

## 10. Suggested build sequence

1. **Engine skeleton + EPUB → chapters** (reuse audiobook parsing). Verify with
   `--list`.
2. **Sentence chunking with smart packing** (5.2). Verify with `--chunks`.
3. **Pinyin annotation** (5.3). Verify with `--pinyin`.
4. **edge-tts synth + cache** (5.5). Verify with `--synth` and playback.
5. **Minimal GUI:** chapter list + reader (current/prev/next) + Play + Next +
   progress + resume. This is already a usable karaoke reader.
6. **Mic capture + Whisper** (5.6, 5.7). Add a Record button.
7. **Grading + green/red + auto-advance** (5.8).
8. **Simplified ⇄ Traditional toggle** (5.4), voice/speed pickers, keyboard
   shortcuts, dark mode.
9. Polish, then apply contingency downgrades where any piece underperforms.

**Milestone 5 is the safe "it works and it's useful" checkpoint.** Everything
after that is the aim-high learning layer, each independently degradable per
Section 9.

---

## 11. Notable design decisions to carry forward

1. **Keep the engine / CLI / GUI split** — zero logic duplication; the CLI is
   your test harness for chunking, pinyin, and grading.
2. **Atomic `.part`→rename + skip-existing cache** for all synthesized audio.
3. **Load Whisper once; transcribe blind** (no `initial_prompt` from the answer).
4. **Align, don't `zip`, when grading** so one missed character doesn't cascade.
5. **Packing only joins, never cuts** — sentence integrity beats hitting a target
   length; there is no hard upper cap.
6. **Convert the display text only**; run pinyin and grading on the *displayed*
   script so the user practices what they see.
7. **Every hard piece has a fallback** and lives behind a clean seam, so the app
   degrades gracefully instead of failing wholesale.
8. **Thread + queue + `after()` poll** for all blocking work; never touch Tk from
   a worker thread.
9. **Persist settings + per-book resume** to a `$HOME` dotfile.

---

*Generated 2026-07-01 as a build spec for a Mandarin-learning EPUB reader,
adapted from the EPUB→Audiobook generator. Point a fresh Claude Code session at
this file to bootstrap the build. Aim high; degrade per Section 9 if needed.*
