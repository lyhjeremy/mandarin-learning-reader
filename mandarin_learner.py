#!/usr/bin/env python3
"""
mandarin_learner.py — Command-line front-end / test harness for the engine
==========================================================================
Headless access to every engine subsystem, so chunking, pinyin, script
conversion, synthesis and grading can be validated without launching the GUI.

Examples
--------
    # List chapters (with junk flags + char counts), detect the script:
    python3 mandarin_learner.py 三生万物.epub --list
    python3 mandarin_learner.py 三生万物.epub --detect

    # Print chunked learning units for chapter 3 (verify 5.2 packing):
    python3 mandarin_learner.py 三生万物.epub --chunks --chapter 3

    # Same, but with per-character pinyin, converted to Traditional:
    python3 mandarin_learner.py 三生万物.epub --pinyin --chapter 3 --convert s2t

    # Pre-cache a chapter's audio (female voice, 0.75x):
    python3 mandarin_learner.py 三生万物.epub --synth --chapter 3 --speed 0.75

    # Test grading / transcription directly:
    python3 mandarin_learner.py --grade "我是學生" "我是學森"
    python3 mandarin_learner.py --transcribe recording.wav --model small
"""

import sys
import argparse

import mandarin_learner_engine as E


def _resolve_voice(v: str) -> str:
    """Accept a full edge voice id, or a friendly VOICES key/substring."""
    if not v:
        return E.DEFAULT_VOICE
    if v in E.VOICES.values():
        return v
    for label, vid in E.VOICES.items():
        if v.lower() in label.lower():
            return vid
    return v


def _load_chapters(path, min_chars):
    book, title, author = E.load_book(path)
    chapters = E.gather_chapters(book, min_chars=min_chars)
    return book, title, author, chapters


def _pick_chapter(chapters, chapter_1based):
    if not chapters:
        sys.exit("No chapters found.")
    if chapter_1based is None:
        return chapters[0]
    for c in chapters:
        if c["index"] == chapter_1based:
            return c
    sys.exit(f"Chapter {chapter_1based} not found (have 1..{chapters[-1]['index']}).")


def cmd_list(args):
    book, title, author, chapters = _load_chapters(args.book, args.min_chars)
    print(f"{title} — {author}")
    print(f"Detected script: {E.detect_script(''.join(c['text'] for c in chapters[:6]))}")
    print(f"{len(chapters)} chapters:\n")
    for c in chapters:
        flag = " [JUNK]" if c["junk"] else ""
        n_chars = E.cjk_count(c["text"])
        print(f"  {c['index']:3d}. {c['title'][:60]:60s} {n_chars:6d} chars{flag}")


def cmd_detect(args):
    book, title, author, chapters = _load_chapters(args.book, args.min_chars)
    sample = "".join(c["text"] for c in chapters if not c["junk"])[:5000]
    print(f"{title}: detected script = {E.detect_script(sample)}")


def cmd_chunks(args):
    book, title, author, chapters = _load_chapters(args.book, args.min_chars)
    ch = _pick_chapter(chapters, args.chapter)
    text = E.convert_script(ch["text"], args.convert)
    units = E.chunk_text_for_learning(text)
    print(f"[{ch['index']}] {ch['title']}  —  {len(units)} learning units "
          f"(convert={args.convert})\n")
    for i, u in enumerate(units):
        print(f"  {i:3d} [{u['chars']:2d}] {u['text']}")


def cmd_pinyin(args):
    book, title, author, chapters = _load_chapters(args.book, args.min_chars)
    ch = _pick_chapter(chapters, args.chapter)
    text = E.convert_script(ch["text"], args.convert)
    units = E.chunk_text_for_learning(text)
    print(f"[{ch['index']}] {ch['title']}  —  {len(units)} units with pinyin\n")
    for i, u in enumerate(units):
        cells = E.annotate(u["text"])
        chars = "".join(c["char"] for c in cells)
        pys = " ".join(c["py"] for c in cells if c["py"])
        print(f"  {i:3d} {chars}")
        print(f"      {pys}")


def cmd_synth(args):
    book, title, author, chapters = _load_chapters(args.book, args.min_chars)
    ch = _pick_chapter(chapters, args.chapter)
    voice = _resolve_voice(args.voice)
    text = E.convert_script(ch["text"], args.convert)
    units = E.chunk_text_for_learning(text)
    print(f"Pre-caching {len(units)} units  voice={voice}  speed={args.speed}x")
    for i, u in enumerate(units):
        cached = E.is_synth_cached(u["text"], voice, args.speed)
        path = E.synth_sentence(u["text"], voice, args.speed)
        tag = "cached" if cached else "synth "
        print(f"  {i:3d} [{tag}] {path}   {u['text'][:24]}")
    print("Done.")


def cmd_grade(args):
    orig, heard = args.grade
    res = E.grade(orig, heard, pinyin_tolerance=args.pinyin_tolerance)
    marks = "".join(("✓" if m["ok"] else ("~" if m["sound"] else "✗"))
                    for m in res["marks"])
    print(f"original : {orig}")
    print(f"heard    : {heard}")
    print(f"marks    : {marks}")
    print(f"accuracy : {res['accuracy']*100:.0f}%   "
          f"auto_advance={res['auto_advance']}")


def cmd_transcribe(args):
    if not E.HAVE_WHISPER:
        sys.exit("No Whisper backend installed (pip install faster-whisper).")
    print(f"Transcribing {args.transcribe} with Whisper '{args.model}' …")
    text = E.transcribe(args.transcribe, model_size=args.model)
    print(f"heard: {text}")


def main():
    p = argparse.ArgumentParser(
        description="Mandarin Learning Reader — CLI / engine test harness.")
    p.add_argument("book", nargs="?", help="path to a Chinese .epub")
    p.add_argument("--list", action="store_true", help="list chapters")
    p.add_argument("--detect", action="store_true", help="detect Simplified/Traditional")
    p.add_argument("--chunks", action="store_true", help="print chunked learning units")
    p.add_argument("--pinyin", action="store_true", help="print units + per-char pinyin")
    p.add_argument("--synth", action="store_true", help="pre-cache chapter audio")
    p.add_argument("--chapter", type=int, help="chapter number (1-based)")
    p.add_argument("--voice", default=E.DEFAULT_VOICE, help="edge voice id or name")
    p.add_argument("--speed", type=float, default=1.0,
                   choices=E.SPEEDS, help="speed tier")
    p.add_argument("--convert", default="none", choices=["none", "s2t", "t2s"],
                   help="Simplified/Traditional conversion of display text")
    p.add_argument("--min-chars", type=int, default=120,
                   help="minimum chars for a section to count as a chapter")
    p.add_argument("--grade", nargs=2, metavar=("ORIGINAL", "HEARD"),
                   help="grade a transcript against the original text")
    p.add_argument("--pinyin-tolerance", action="store_true",
                   help="count right-sound/wrong-char as a soft match when grading")
    p.add_argument("--transcribe", metavar="WAV", help="transcribe a WAV file")
    p.add_argument("--model", default=E.WHISPER_MODEL_DEFAULT,
                   help="whisper model size (tiny/base/small/medium)")
    args = p.parse_args()

    if args.grade:
        return cmd_grade(args)
    if args.transcribe:
        return cmd_transcribe(args)

    missing = E.missing_deps()
    if missing:
        sys.exit("Missing required packages: " + ", ".join(missing))

    if not args.book:
        p.error("a book path is required (or use --grade / --transcribe)")

    if args.list:
        return cmd_list(args)
    if args.detect:
        return cmd_detect(args)
    if args.chunks:
        return cmd_chunks(args)
    if args.pinyin:
        return cmd_pinyin(args)
    if args.synth:
        return cmd_synth(args)

    # Default action: a quick overview.
    return cmd_list(args)


if __name__ == "__main__":
    main()
