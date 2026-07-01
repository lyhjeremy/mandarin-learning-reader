"""
mandarin_learner_engine.py — Core engine for the Mandarin Learning Reader
==========================================================================
All the non-UI logic for turning a Chinese `.epub` into an interactive
Mandarin-learning environment. Shared by both front-ends:

    mandarin_learner.py         (command line / testing)
    mandarin_learner_gui.py     (Tkinter GUI, karaoke read-along + mic grading)

This engine reuses the proven pieces of the EPUB→Audiobook generator (EPUB
parsing, chapter/junk detection, CJK language logic, the edge-tts
retry+atomic+skip-existing reliability contract) and adds the new
Mandarin-learning subsystems:

    • chunk_text_for_learning() — CJK sentence chunking with smart packing
    • annotate()                — per-character tone-marked pinyin (pypinyin)
    • detect_script/convert     — Simplified ⇄ Traditional (OpenCC)
    • synth_sentence()          — per-sentence edge-tts synthesis, cached
    • record_wav()              — microphone capture (sounddevice)
    • transcribe()              — offline local Whisper speech-to-text
    • grade()                   — character-level green/red pronunciation grading
    • progress store            — ~/.mandarin_learner.json settings + resume

Nothing here imports a GUI toolkit, so it is safe to import from anywhere.
Every hard piece degrades gracefully: if pypinyin / OpenCC / faster-whisper /
sounddevice are missing, the relevant feature reports itself unavailable
instead of crashing the whole app (see Section 9 of the project spec).

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4 pypinyin faster-whisper \
                sounddevice scipy pygame
    pip install opencc-python-reimplemented      # optional: script toggle
"""

import os
import re
import sys
import json
import time
import hashlib
import asyncio
import unicodedata
import tempfile
import datetime
from pathlib import Path


# ── Dependency probing ────────────────────────────────────────────────────────
# Core EPUB + TTS deps are imported eagerly (the app is useless without them);
# the learning extras are probed lazily so a missing one disables just its
# feature rather than the whole engine.

REQUIRED_DEPS = [("edge-tts", "edge_tts"),
                 ("ebooklib", "ebooklib"),
                 ("beautifulsoup4", "bs4")]


def missing_deps():
    """Return the pip names of any *required* package that won't import."""
    missing = []
    for pkg, imp in REQUIRED_DEPS:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    return missing


import ebooklib                       # noqa: E402
from ebooklib import epub             # noqa: E402
from bs4 import BeautifulSoup         # noqa: E402
import edge_tts                       # noqa: E402


def _have(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


HAVE_PYPINYIN = _have("pypinyin")
HAVE_OPENCC = _have("opencc")
HAVE_SOUNDDEVICE = _have("sounddevice")
HAVE_FASTER_WHISPER = _have("faster_whisper")
HAVE_OPENAI_WHISPER = _have("whisper")
HAVE_WHISPER = HAVE_FASTER_WHISPER or HAVE_OPENAI_WHISPER


# ── Constants ─────────────────────────────────────────────────────────────────

# Mandarin voices (male + female selectable); female Xiaoxiao is the default.
VOICES = {
    "Female — Xiaoxiao (warm)": "zh-CN-XiaoxiaoNeural",
    "Male — Yunxi": "zh-CN-YunxiNeural",
    "Female — Xiaoyi": "zh-CN-XiaoyiNeural",
    "Male — Yunyang (news)": "zh-CN-YunyangNeural",
    "Female — Taiwan (HsiaoChen)": "zh-TW-HsiaoChenNeural",
    "Male — Taiwan (YunJhe)": "zh-TW-YunJheNeural",
}
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# Exactly five speed tiers, mapped to edge-tts's own `rate` string (no ffmpeg).
SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5]
RATE_FOR_SPEED = {0.5: "-50%", 0.75: "-25%", 1.0: "+0%", 1.25: "+25%", 1.5: "+50%"}

AUTO_ADVANCE_THRESHOLD = 0.90         # ≥90% accuracy auto-advances

RETRY_LIMIT = 4
RETRY_DELAY = 3                        # seconds between retries (× attempt #)

WHISPER_MODEL_DEFAULT = "small"       # tiny | base | small | medium

CACHE_DIR = Path(tempfile.gettempdir()) / "mandarin_learner_tts_cache"
PROGRESS_FILE = Path.home() / ".mandarin_learner.json"

DROP_TAGS = {"script", "style", "head", "figure", "figcaption",
             "img", "svg", "aside", "nav", "footer"}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "tr", "br", "blockquote", "section", "article"}

# Front/back matter — flagged (junk=True), never silently dropped.
JUNK_PATTERNS = [
    "cover", "title page", "half title", "copyright", "colophon", "imprint",
    "index", "acknowledg", "about the author", "about the publisher",
    "also by", "praise for", "front matter", "back matter", "dedication",
    "table of contents", "contents",
    "目录", "目錄", "版权", "版權", "封面", "扉页", "扉頁", "致谢", "致謝",
    "索引", "关于作者", "關於作者", "版权页", "版權頁",
]

# CJK sentence terminators / pausers to split on (spec 5.2).
CJK_TERMINATORS = "。！？；…、—"


# ── Text extraction (reused from the audiobook engine) ────────────────────────

def extract_text(html_bytes: bytes) -> str:
    """Parse one EPUB HTML document into clean, readable plain text."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text(separator=" ")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")   # soft hyphen
    text = text.replace("�", "")   # decode-failure replacement char
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s*\d+\s*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def first_heading(html_bytes: bytes) -> str:
    """First h1..h3 heading text in a document, or '' — a title fallback."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3"]):
        txt = re.sub(r"\s+", " ", tag.get_text(" ")).strip()
        if txt:
            return txt
    return ""


# ── Language detection (CJK) ──────────────────────────────────────────────────

def _is_cjk_ideograph(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or       # CJK Unified Ideographs
            0x3400 <= o <= 0x4DBF or       # Extension A
            0xF900 <= o <= 0xFAFF)         # Compatibility Ideographs


def cjk_count(text: str) -> int:
    """Number of CJK ideographs (the 'length' metric for packing)."""
    return sum(1 for ch in text if _is_cjk_ideograph(ch))


def cjk_fraction(text: str) -> float:
    """Fraction of alphabetic characters that are CJK ideographs."""
    cjk = letters = 0
    for ch in text:
        if _is_cjk_ideograph(ch):
            cjk += 1
            letters += 1
        elif ch.isalpha():
            letters += 1
    return cjk / letters if letters else 0.0


def is_cjk_text(text: str) -> bool:
    """True when the text is predominantly Chinese."""
    return cjk_fraction(text) > 0.2


# ── Table-of-contents helpers (reused) ────────────────────────────────────────

def flatten_toc(toc) -> list:
    """Flatten an ebooklib TOC into [(title, href_basename), ...]."""
    entries = []
    for item in toc:
        if isinstance(item, tuple):
            section, children = item
            if hasattr(section, "title") and hasattr(section, "href"):
                href = section.href.split("#")[0].split("/")[-1]
                entries.append((section.title.strip(), href))
            entries.extend(flatten_toc(children))
        elif hasattr(item, "title") and hasattr(item, "href"):
            href = item.href.split("#")[0].split("/")[-1]
            entries.append((item.title.strip(), href))
    return entries


def build_title_map(toc_entries: list) -> dict:
    seen = {}
    for title, href in toc_entries:
        if href not in seen:
            seen[href] = title
    return seen


def _looks_like_junk(title: str, basename: str, char_count: int,
                     min_chars: int) -> bool:
    hay = f"{title} {basename}".lower()
    if any(pat in hay for pat in JUNK_PATTERNS):
        return True
    if char_count < max(min_chars * 2, 400):
        return True
    return False


# ── Chapter assembly (reused) ─────────────────────────────────────────────────

def gather_chapters(book, min_chars: int = 120) -> list:
    """
    Walk the spine in reading order ->
        [{index, title, text, file_name, junk}]
    Title order: TOC title -> first in-document heading -> "Section N".
    """
    title_map = build_title_map(flatten_toc(book.toc))
    chapters, chapter_num = [], 0
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        content = item.get_content()
        text = extract_text(content)
        if len(text) < min_chars:
            continue
        chapter_num += 1
        basename = Path(item.file_name).name
        title = (title_map.get(basename)
                 or first_heading(content)
                 or f"Section {chapter_num}")
        chapters.append({
            "index": chapter_num,
            "title": title,
            "text": text,
            "file_name": basename,
            "junk": _looks_like_junk(title, basename, len(text), min_chars),
        })
    return chapters


def load_book(path):
    """Read an EPUB and return (book, title, author)."""
    book = epub.read_epub(str(path))
    title = book.title or Path(path).stem
    authors = book.get_metadata("DC", "creator")
    author = authors[0][0] if authors else "Unknown"
    return book, title, author


# ── 5.2  CJK sentence chunking with smart packing ─────────────────────────────

def split_raw_sentences(text: str) -> list:
    """
    Split text into raw sentences at CJK terminators (。！？；…、—), keeping the
    terminator attached to the sentence it ends. Newlines also break a unit.
    """
    out = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            if "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        if ch in CJK_TERMINATORS:
            # Absorb any run of consecutive terminators (…… / —— / ！？).
            while i + 1 < n and text[i + 1] in CJK_TERMINATORS:
                i += 1
                buf.append(text[i])
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def chunk_text_for_learning(text, target_min=7, target_max=15) -> list:
    """
    Split Chinese text into learning units.

    Strategy:
      1. Split into raw sentences at CJK terminators (。！？；…、—).
      2. Walk sentences left-to-right, packing SHORT ones together: while the
         current unit is shorter than target_min AND appending the next
         sentence keeps it within target_max, join them. This only ever *joins*
         short neighbours; it NEVER splits a real sentence to hit target_max.
      3. Long single sentences (> target_max) pass through untouched (no cap).

    Returns: [ {"text": str, "chars": int}, ... ]   (chars = CJK ideograph count)

    Examples:
      "我很開心。你呢？"      -> [ {"text": "我很開心。你呢？", "chars": 6} ]
      "學習很重要。這是一個關於中文學習的很長句子。"
                            -> [ {"text": "學習很重要。", "chars": 5},
                                 {"text": "這是一個關於中文學習的很長句子。", "chars": 15} ]
    """
    sentences = split_raw_sentences(text)
    units = []
    i = 0
    n = len(sentences)
    while i < n:
        cur = sentences[i]
        cur_len = cjk_count(cur)
        i += 1
        # Pack short neighbours only: join while current is under target_min and
        # the merged unit would still fit comfortably under target_max.
        while (i < n and cur_len < target_min
               and cur_len + cjk_count(sentences[i]) <= target_max):
            cur += sentences[i]
            cur_len += cjk_count(sentences[i])
            i += 1
        units.append({"text": cur, "chars": cur_len})
    return units


# ── 5.3  Pinyin annotation (per character, context-aware) ─────────────────────

def annotate(text: str) -> list:
    """
    Annotate each character with its tone-marked pinyin.

        "我是學生" -> [ {"char":"我","py":"wǒ"}, {"char":"是","py":"shì"},
                       {"char":"學","py":"xué"}, {"char":"生","py":"shēng"} ]

    Whole CJK runs are passed to pypinyin together so phrase context can
    disambiguate heteronyms (多音字); non-CJK chars (punctuation, spaces, latin)
    come back with py="". Returns chars with py="" if pypinyin is unavailable.
    """
    if not HAVE_PYPINYIN:
        return [{"char": ch, "py": ""} for ch in text]
    from pypinyin import pinyin, Style
    out = []
    i = 0
    n = len(text)
    while i < n:
        if _is_cjk_ideograph(text[i]):
            j = i
            while j < n and _is_cjk_ideograph(text[j]):
                j += 1
            run = text[i:j]
            py = pinyin(run, style=Style.TONE, heteronym=False,
                        errors=lambda x: [c for c in x])
            for k, ch in enumerate(run):
                p = py[k][0] if k < len(py) and py[k] else ""
                out.append({"char": ch, "py": p})
            i = j
        else:
            out.append({"char": text[i], "py": ""})
            i += 1
    return out


def pinyin_of(text: str, tone: bool = True) -> str:
    """Flat pinyin string for a phrase (used by grading's sound-tolerance)."""
    if not HAVE_PYPINYIN:
        return ""
    from pypinyin import lazy_pinyin, Style
    style = Style.TONE3 if tone else Style.NORMAL
    return " ".join(lazy_pinyin(text, style=style, errors="ignore"))


def _char_pinyin(ch: str, tone: bool) -> str:
    if not HAVE_PYPINYIN or not _is_cjk_ideograph(ch):
        return ""
    from pypinyin import pinyin, Style
    style = Style.TONE3 if tone else Style.NORMAL
    got = pinyin(ch, style=style, heteronym=False)
    return got[0][0] if got and got[0] else ""


# ── 5.4  Script detection & Simplified ⇄ Traditional conversion (OpenCC) ──────

_CC = {}


def _opencc(mode: str):
    """Cached OpenCC converter for 's2t' / 't2s', or None if unavailable."""
    if not HAVE_OPENCC:
        return None
    if mode not in _CC:
        try:
            from opencc import OpenCC
            try:
                _CC[mode] = OpenCC(mode)
            except Exception:
                _CC[mode] = OpenCC(mode + ".json")   # some builds want .json
        except Exception:
            _CC[mode] = None
    return _CC[mode]


def convert_script(text: str, mode: str) -> str:
    """Convert display text. mode in {'none','s2t','t2s'}. Safe no-op on error."""
    if mode == "none" or not text:
        return text
    cc = _opencc(mode)
    if cc is None:
        return text
    try:
        return cc.convert(text)
    except Exception:
        return text


def detect_script(text: str) -> str:
    """
    Best-effort label for a text sample: 'simplified', 'traditional', 'mixed',
    or 'unknown'. Compares how many characters s2t vs t2s conversion changes.
    """
    if not HAVE_OPENCC:
        return "unknown"
    sample = "".join(ch for ch in text if _is_cjk_ideograph(ch))[:2000]
    if not sample:
        return "unknown"
    s2t = _opencc("s2t")
    t2s = _opencc("t2s")
    if s2t is None or t2s is None:
        return "unknown"
    try:
        changed_by_s2t = sum(a != b for a, b in zip(sample, s2t.convert(sample)))
        changed_by_t2s = sum(a != b for a, b in zip(sample, t2s.convert(sample)))
    except Exception:
        return "unknown"
    total = len(sample) or 1
    # Many chars change under s2t  -> the text was simplified (had simp-only forms).
    # Many chars change under t2s  -> the text was traditional.
    if changed_by_s2t > changed_by_t2s and changed_by_s2t / total > 0.02:
        return "simplified"
    if changed_by_t2s > changed_by_s2t and changed_by_t2s / total > 0.02:
        return "traditional"
    return "mixed"


# ── 5.5  edge-tts Mandarin synthesis (per sentence, cached, atomic, retrying) ─

async def _synth_save(text: str, path: str, voice: str, rate: str):
    await edge_tts.Communicate(text, voice=voice, rate=rate).save(path)


def _retrying(coro_factory, path: str):
    """Run an async synth coroutine with retry + empty-file contract."""
    last = None
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            result = asyncio.run(coro_factory())
            if os.path.getsize(path) == 0:
                raise RuntimeError("edge-tts wrote an empty file")
            return result
        except Exception as exc:
            last = exc
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY * attempt)
    raise RuntimeError(f"edge-tts failed after {RETRY_LIMIT} attempts: {last}")


def _cache_key(text: str, voice: str, rate: str) -> str:
    h = hashlib.sha1(f"{text}|{voice}|{rate}".encode("utf-8")).hexdigest()[:20]
    return h


def cached_sentence_path(text: str, voice: str, speed: float) -> Path:
    rate = RATE_FOR_SPEED.get(speed, "+0%")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_cache_key(text, voice, rate)}.mp3"


def is_synth_cached(text: str, voice: str, speed: float) -> bool:
    p = cached_sentence_path(text, voice, speed)
    return p.exists() and p.stat().st_size > 0


def synth_sentence(text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> str:
    """
    Return a path to a cached MP3 for `text`, synthesising it (with retry +
    atomic .part->rename) if absent. Cache key is hash(text|voice|rate), so
    replays and re-opening a chapter are instant.
    """
    rate = RATE_FOR_SPEED.get(speed, "+0%")
    out = cached_sentence_path(text, voice, speed)
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    staging = out.with_suffix(".part")
    _retrying(lambda: _synth_save(text, str(staging), voice, rate), str(staging))
    os.replace(staging, out)
    return str(out)


# ── 5.6  Microphone capture (sounddevice) ─────────────────────────────────────

SR = 16000   # 16 kHz mono is ideal for Whisper


def record_wav(path: str, seconds: float = 8.0) -> str:
    """Record `seconds` of mono 16 kHz audio to a WAV Whisper can read."""
    if not HAVE_SOUNDDEVICE:
        raise RuntimeError("sounddevice is not installed — cannot record audio.")
    import sounddevice as sd
    import numpy as np
    audio = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    _write_wav(path, audio, SR)
    return path


class Recorder:
    """
    Start/stop microphone recorder for a Record→Stop toggle in the GUI.
    Records until stop() is called (or `max_seconds`), then writes a WAV.
    """

    def __init__(self, max_seconds: float = 30.0):
        if not HAVE_SOUNDDEVICE:
            raise RuntimeError("sounddevice is not installed — cannot record.")
        import sounddevice as sd
        self._sd = sd
        self._frames = []
        self._stream = None
        self._max = max_seconds
        self._started = 0.0

    def start(self):
        self._frames = []
        self._started = time.monotonic()

        def _cb(indata, frames, time_info, status):
            self._frames.append(indata.copy())
            if time.monotonic() - self._started > self._max:
                raise self._sd.CallbackStop()

        self._stream = self._sd.InputStream(samplerate=SR, channels=1,
                                            dtype="int16", callback=_cb)
        self._stream.start()

    def stop(self, path: str) -> str:
        import numpy as np
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._frames:
            audio = np.concatenate(self._frames, axis=0)
        else:
            import numpy as np
            audio = np.zeros((1, 1), dtype="int16")
        _write_wav(path, audio, SR)
        return path


def _write_wav(path: str, audio, sr: int):
    """Write int16 samples to a WAV (scipy preferred, soundfile fallback)."""
    try:
        import scipy.io.wavfile as wav
        wav.write(path, sr, audio)
        return
    except Exception:
        pass
    import soundfile as sf
    sf.write(path, audio, sr, subtype="PCM_16")


# ── 5.7  Local Whisper speech-to-text (offline, loaded once) ──────────────────

class WhisperEngine:
    """
    Lazily loads a local Whisper model ONCE and keeps it in memory. Prefers
    faster-whisper (CTranslate2, fast, low RAM); falls back to openai-whisper.
    Transcribes BLIND (no initial_prompt) so grading isn't biased toward the
    'correct' text.
    """

    def __init__(self, model_size: str = WHISPER_MODEL_DEFAULT):
        self.model_size = model_size
        self._backend = None
        self._model = None

    @property
    def available(self) -> bool:
        return HAVE_WHISPER

    def load(self):
        if self._model is not None:
            return
        if HAVE_FASTER_WHISPER:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device="cpu",
                                       compute_type="int8")
            self._backend = "faster"
        elif HAVE_OPENAI_WHISPER:
            import whisper
            self._model = whisper.load_model(self.model_size)
            self._backend = "openai"
        else:
            raise RuntimeError("No Whisper backend installed "
                               "(pip install faster-whisper).")

    def transcribe(self, wav_path: str) -> str:
        self.load()
        if self._backend == "faster":
            segments, _ = self._model.transcribe(wav_path, language="zh",
                                                 beam_size=1)
            return "".join(seg.text for seg in segments).strip()
        result = self._model.transcribe(wav_path, language="zh", beam_size=1)
        return (result.get("text") or "").strip()


_DEFAULT_WHISPER = None


def transcribe(wav_path: str, model_size: str = WHISPER_MODEL_DEFAULT) -> str:
    """Convenience: transcribe with a module-level singleton model."""
    global _DEFAULT_WHISPER
    if _DEFAULT_WHISPER is None or _DEFAULT_WHISPER.model_size != model_size:
        _DEFAULT_WHISPER = WhisperEngine(model_size)
    return _DEFAULT_WHISPER.transcribe(wav_path)


# ── 5.8  Character-level pronunciation grading (green / red / amber) ───────────

def _cjk_only(s: str) -> list:
    return [c for c in s if _is_cjk_ideograph(c)]


def _to_simplified(chars: list) -> list:
    """Per-character Simplified form (keeps 1:1 length for alignment)."""
    cc = _opencc("t2s")
    if cc is None:
        return chars
    out = []
    for c in chars:
        try:
            conv = cc.convert(c)
        except Exception:
            conv = c
        out.append(conv[0] if conv else c)
    return out


def grade(original_text: str, heard_text: str, pinyin_tolerance: bool = False,
          tone_sensitive: bool = False, normalize_script: bool = True) -> dict:
    """
    Align original vs. heard at the character level (difflib, NOT a naive zip, so
    one missed character doesn't cascade) and mark each ORIGINAL character.

    Returns:
      {
        "accuracy": 0.83,
        "auto_advance": False,                 # True iff accuracy >= 0.90
        "heard": "…",                          # CJK-only transcript, for display
        "marks": [ {"char":"我","ok":True,"sound":False}, ... ]
      }

    ok=True  -> exact character match          (green)
    sound=True (ok=False) -> right sound, wrong character, only when
                pinyin_tolerance is on         (amber; counts toward accuracy)

    Whisper often transcribes Mandarin into Traditional characters regardless of
    the source script, so a Simplified sentence read correctly would otherwise be
    marked wrong (学 vs 學). With normalize_script=True both sides are compared in
    a common (Simplified) script for equality, while the ORIGINAL display
    characters are what's shown/coloured.
    """
    import difflib
    orig = _cjk_only(original_text)
    heard = _cjk_only(heard_text)
    marks = [{"char": c, "ok": False, "sound": False} for c in orig]

    # Compare in a common script so 学==學 (display keeps the original chars).
    if normalize_script and HAVE_OPENCC:
        orig_cmp = _to_simplified(orig)
        heard_cmp = _to_simplified(heard)
    else:
        orig_cmp, heard_cmp = orig, heard

    sm = difflib.SequenceMatcher(a=orig_cmp, b=heard_cmp, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                marks[i]["ok"] = True
        elif tag == "replace" and pinyin_tolerance:
            # Compare pinyin of aligned chars in the replaced span.
            span = min(i2 - i1, j2 - j1)
            for k in range(span):
                oc = orig[i1 + k]
                hc = heard[j1 + k]
                if (_char_pinyin(oc, tone_sensitive)
                        and _char_pinyin(oc, tone_sensitive)
                        == _char_pinyin(hc, tone_sensitive)):
                    marks[i1 + k]["sound"] = True

    correct = sum(1 for m in marks if m["ok"] or (pinyin_tolerance and m["sound"]))
    total = len(orig) or 1
    acc = correct / total
    return {
        "accuracy": acc,
        "auto_advance": acc >= AUTO_ADVANCE_THRESHOLD,
        "heard": "".join(heard),
        "marks": marks,
    }


# ── 5.9  Progress + settings persistence (~/.mandarin_learner.json) ───────────

DEFAULT_SETTINGS = {
    "voice": DEFAULT_VOICE,
    "speed": 1.0,
    "script_mode": "none",         # none | s2t | t2s
    "whisper_model": WHISPER_MODEL_DEFAULT,
    "record_seconds": 8.0,
    "pinyin_tolerance": False,
    "tone_sensitive": False,
    "theme": "light",
    "auto_advance": True,
}


def load_progress() -> dict:
    """Load the whole progress/settings dotfile (with defaults filled in)."""
    data = {}
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(data.get("settings", {}))
    return {
        "last_book": data.get("last_book"),
        "settings": settings,
        "books": data.get("books", {}),
    }


def save_progress(data: dict):
    """Atomically write the progress/settings dotfile."""
    staging = PROGRESS_FILE.with_suffix(".json.tmp")
    try:
        staging.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        os.replace(staging, PROGRESS_FILE)
    except OSError:
        pass


def record_position(book_path: str, title: str, chapter_index: int,
                    sentence_index: int, settings: dict = None):
    """Update saved position for a book (and optionally settings), then flush."""
    data = load_progress()
    data["last_book"] = book_path
    if settings:
        data["settings"].update(settings)
    data.setdefault("books", {})[book_path] = {
        "title": title,
        "chapter_index": chapter_index,
        "sentence_index": sentence_index,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    save_progress(data)


def get_position(book_path: str) -> dict:
    """Saved {chapter_index, sentence_index} for a book, or zeros."""
    data = load_progress()
    rec = data.get("books", {}).get(book_path, {})
    return {
        "chapter_index": rec.get("chapter_index", 0),
        "sentence_index": rec.get("sentence_index", 0),
    }


# ── Module self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    demo = "我很開心。你呢？學習很重要。這是一個關於中文學習的很長句子。"
    print("Chunks:")
    for u in chunk_text_for_learning(demo):
        print(f"  [{u['chars']:2d}] {u['text']}")
    print("\nPinyin of 我是學生:")
    print("  ", annotate("我是學生"))
    print("\nGrade 我是學生 vs 我是學森:")
    print("  ", grade("我是學生", "我是學森"))
