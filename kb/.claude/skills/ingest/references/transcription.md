# Transcription

Local, no API spend: `scripts/transcribe.py` wraps faster-whisper in a venv kept outside the repo (setup in AGENTS.md -> Transcribing audio and video).

```bash
yt-dlp -x -o "<name>.%(ext)s" "<url>"          # podcasts, uploaded video, recorded live audio
./scripts/transcribe.py <file> [-m small|large-v3-turbo] [-l en]
```

- The default model is `large-v3-turbo`; `-m small` is usually enough on clear speech and much faster.
- Leave `-l` off for non-English or mixed-language sources; it auto-detects.
- Output is timestamped `[MM:SS] text`. Save it to `raw/sources/<Author - @handle>/` with the provenance header, including a speaker guide (most ASR does not tag speakers).
- Check for an existing transcript before transcribing again.
