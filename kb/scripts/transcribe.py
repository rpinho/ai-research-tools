#!/usr/bin/env python3
"""Transcribe local audio/video to text (faster-whisper, CPU, Apple Silicon).

Closes the ASR gap the bookmark-ingest skill flagged: X-only video, program
recordings, voice notes -- anything ffmpeg can read but the archive could not.

    ./transcribe.py <media file> [-o out.txt] [-m MODEL] [-l LANG]

MODEL  default large-v3-turbo (best quality/speed on M-series CPU).
       Use 'small' for a fast pass, 'large-v3' for maximum accuracy.
LANG   omit to auto-detect (works for non-English sources);
       pass e.g. 'en' to skip detection and go slightly faster.

Requires the dedicated env: ~/.local/share/whisper-env  (uv-managed, outside
the repo so nothing here gets committed). ffmpeg must be on PATH.
"""
import argparse, os, subprocess, sys, shutil, time

VENV_PY = os.path.expanduser("~/.local/share/whisper-env/bin/python")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("-o", "--out")
    ap.add_argument("-m", "--model", default="large-v3-turbo")
    ap.add_argument("-l", "--lang", default=None)
    a = ap.parse_args()

    if not os.path.exists(a.media):
        sys.exit(f"no such file: {a.media}")
    if not os.path.exists(VENV_PY):
        sys.exit(f"whisper env missing at {VENV_PY}\n  uv venv --python 3.12 ~/.local/share/whisper-env"
                 f"\n  uv pip install --python {VENV_PY} faster-whisper")
    if not shutil.which("ffmpeg") and not os.path.exists(os.path.expanduser("~/.local/bin/ffmpeg")):
        sys.exit("ffmpeg not found (expected ~/.local/bin/ffmpeg)")

    out = a.out or os.path.splitext(a.media)[0] + ".txt"
    # run inside the venv so this script itself stays dependency-free
    code = f'''
import sys, time
from faster_whisper import WhisperModel
t0 = time.time()
model = WhisperModel({a.model!r}, device="cpu", compute_type="int8")
segs, info = model.transcribe({a.media!r}, language={a.lang!r}, vad_filter=True,
                              beam_size=5, condition_on_previous_text=False)
print(f"[lang={{info.language}} p={{info.language_probability:.2f}} dur={{info.duration:.0f}}s]",
      file=sys.stderr)
lines = []
for s in segs:
    ts = f"[{{int(s.start)//60:02d}}:{{int(s.start)%60:02d}}]"
    lines.append(f"{{ts}} {{s.text.strip()}}")
    print(f"{{ts}} {{s.text.strip()}}", file=sys.stderr, flush=True)
open({out!r}, "w").write("\\n".join(lines) + "\\n")
print(f"[done in {{time.time()-t0:.0f}}s -> {out!r}]", file=sys.stderr)
'''
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
    sys.exit(subprocess.call([VENV_PY, "-c", code], env=env))

if __name__ == "__main__":
    main()
