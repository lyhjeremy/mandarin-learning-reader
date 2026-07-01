# Mandarin Learning Reader

Turn any Chinese `.epub` e-book into an **interactive Mandarin tutor**. Sentences
appear one at a time, **karaoke-style, with pinyin under every character**. You
hear each one read aloud in a natural Mandarin voice, **read along**, then
**speak it back** — a local, offline **Whisper** model listens and grades your
pronunciation **character-by-character** (green = right, red = wrong,
amber = right sound / wrong character). Score **≥ 90 %** and it auto-advances.
Free, runs on your own computer.

> 🌐 **Non-technical overview:** see the showcase page →
> https://lyhjeremy.github.io/mandarin-learning-reader/

![The reader, mid-lesson](docs/reader.png)

---

## What it does

- 📖 **Reads Chinese EPUBs** — extracts the text, detects chapters, and flags
  front/back matter (目录, 版权, 封面).
- ✂️ **Smart sentence chunking** — splits on Chinese punctuation (`。！？；…、—`)
  and packs very short lines together into comfortable ~7–15-character units,
  without ever cutting a real sentence.
- 🀄 **Pinyin over every character** — tone-marked, context-aware readings via
  `pypinyin`.
- 🗣️ **Natural Mandarin voices** via Microsoft `edge-tts` (no API key, no cost) —
  male/female, Mainland/Taiwan, five speeds (0.5×–1.5×).
- 🎤 **Speak-back grading** — a local, offline Whisper model transcribes your
  voice and aligns it against the sentence, painting each character green / red /
  amber. **No audio ever leaves your machine.**
- 🎯 **Auto-advance on ≥ 90 %** — or turn it off and step through manually.
- 🔁 **Simplified ⇄ Traditional** — flip the displayed script either way (OpenCC);
  grading normalises script, so a correctly-read 学 and 學 both count.
- 💾 **Resume** — reopens your last book, chapter, and sentence; settings persist.
- 🎛️ **Built for study** — light/dark themes, keyboard shortcuts, and lenient
  (sound-based) grading you can toggle on.

## Repository layout

This repo contains **code only**. The `.epub` source books are intentionally
**not** included (third-party copyright) — point the app at your own Chinese
EPUBs.

```
mandarin_learner_gui.py       🖱️  The app: chapter list + karaoke reader + mic grading
mandarin_learner.py           ⌨️  Command-line version / engine test harness
mandarin_learner_engine.py    🧠  Engine: parsing, chunking, pinyin, TTS, Whisper, grading
requirements.txt              📦  Python dependencies
MANDARIN_LEARNER_OVERVIEW.md  📐  Full design spec / build write-up
docs/reader.png               🖼️  Screenshot
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the app
python3 mandarin_learner_gui.py
```

The first time you press **Record**, Whisper downloads a speech model once
(~460 MB for `small`); everything after that runs offline. `edge-tts` needs
internet to synthesize each *new* sentence — the audio is then cached, so
replays and re-opening a chapter are instant. On macOS you may need to grant
**Microphone** permission to your terminal (System Settings → Privacy).

### Keyboard shortcuts

`Space` = Play · `R` = Record / Stop · `→` / `←` = next / previous sentence ·
`Enter` = Replay · `Esc` = Stop.

### Command line (for testing the engine)

```bash
python3 mandarin_learner.py book.epub --list                 # chapters + junk flags
python3 mandarin_learner.py book.epub --chunks --chapter 3   # chunked learning units
python3 mandarin_learner.py book.epub --pinyin --chapter 3 --convert s2t
python3 mandarin_learner.py --grade "我是學生" "我是學森"     # test grading directly
```

## How it works

The learning loop is **read → listen → speak → grade**:

1. **Open a Chinese EPUB.** The app detects the script (Simplified / Traditional),
   lists chapters, and chunks each into short sentence units.
2. **Read along.** The current sentence is shown large and centred with pinyin
   above every character; the previous and next lines sit dim for context.
3. **Listen.** Play the sentence in a natural Mandarin voice at your chosen speed.
4. **Speak it back.** Record yourself; an offline Whisper model transcribes you.
5. **See your score.** Each character turns green (right), red (wrong), or amber
   (right sound, wrong character). Hit ≥ 90 % and it moves on automatically.

Under the hood it reuses the proven pipeline of its sibling project,
[epub-to-audiobook](https://github.com/lyhjeremy/epub-to-audiobook) — the same
EPUB parsing, CJK language logic, and the `edge-tts` retry + atomic-cache
contract — with a clean **engine / CLI / GUI** split so all the logic lives in
one place. Full design notes are in
[`MANDARIN_LEARNER_OVERVIEW.md`](MANDARIN_LEARNER_OVERVIEW.md).

## Graceful degradation

Every hard piece degrades on its own seam: no microphone / `faster-whisper` →
recording disables but the read-along still works; no `opencc` → the script
toggle hides; `faster-whisper` missing but `openai-whisper` present → it's used
automatically.

## A note on content

This tool is for studying with books **you own or that are in the public
domain**. The repository ships no copyrighted books or audio; you supply your own
EPUB files. All speech recognition runs **locally** — your microphone audio is
never uploaded.

## License

[MIT](LICENSE) © 2026 Jeremy Lee
