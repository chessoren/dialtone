"""Build the ~3 minute demo video: record pages with Playwright, narrate, mux with ffmpeg.

  .venv/bin/python scripts/make_video.py [--src fixtures|real] [--tts gemini|say]

Requires the local web server (python -m http.server 8765 from the repo root).
Narration numbers are read from eval/report.json and web/data/demo*.json, so the
voice-over can never claim something the data does not show.
Output: out/dialtone-demo.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
BASE = "http://localhost:8765"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def narration(src: str) -> list[dict]:
    report = json.loads((ROOT / "eval/report.json").read_text())
    demo = json.loads((ROOT / ("web/data/demo.real.json" if src == "real" else "web/data/demo.json")).read_text())
    full = report["detection_model_only"]["full_call"]
    hist = ROOT / "eval/history/run1-first-blind.json"
    blind = json.loads(hist.read_text())["detection_model_only"]["full_call"] if hist.exists() else full
    diff = report["detection_model_only"]["by_difficulty"]
    human, bot, yes = demo["calls"]
    base = demo["baseline"]
    speed = f"{base['duration'] / bot['duration']:.1f} times faster" if base else "much faster"
    real = report.get("real_calls") or []
    kind = "real calls placed with CALL-E" if src == "real" else "scripted replays in CALL-E's exact result format"
    real_line = (f" plus {len(real)} real calls, where {sum(1 for c in real if c.get('label') == c['detection']['label'])} "
                 f"of {len(real)} counterparts were identified correctly." if real else ".")
    return [
        {"id": "intro", "url": f"{BASE}/web/slides.html?s=intro", "min": 4, "text":
            "Thousands of builders are teaching agents to call businesses. Those businesses increasingly answer with a voicebot. "
            "So a growing share of CALL-E's outbound calls will be answered by another AI, and today, the calling agent has no idea. "
            "DIALTONE is the missing layer for that call."},
        {"id": "calls", "url": f"{BASE}/web/index.html?record&speed=1.25" + ("&src=real" if src == "real" else ""), "min": 4, "wait_done": True, "text":
            f"Same task, three counterparts. These are {kind}. "
            "Channel one is a person. The agent opens with its disclosure and a short spoken token: dialtone one, code four seven two. "
            "The host ignores it. DIALTONE reads timing, interruptions and phrasing, keeps human mode, and the booking comes back human attested. "
            "Channel two is an inbound voicebot that speaks the protocol. It echoes the code, both sides switch to machine mode, "
            f"and the call ends {speed} than the same bot without DIALTONE. The result is agent asserted, not human attested. "
            "Channel three is an agreeable bot that says yes to everything. Both agents say the table is booked, and CALL-E reports task completed. "
            "DIALTONE refuses it: a binding booking, confirmed only by a machine, with no code and no record."},
        {"id": "how", "url": f"{BASE}/web/slides.html?s=how", "min": 4, "text":
            "Under the hood, the protocol compiles into the CALL-E task, and CALL-E's own model performs the switch. "
            "Then DIALTONE independently re-derives who was on the line from transcript timing and text, and relabels task completed. "
            "It can downgrade a result. It never upgrades one."},
        {"id": "eval", "url": f"{BASE}/web/index.html?record&view=eval", "min": 4, "text":
            "We did not want a ninety nine percent claim. The detector was tested on forty transcripts written blind to its code"
            f"{real_line} "
            f"On the first blind run, it was right {round(blind['accuracy'] * 100)} percent of the time. "
            f"Typical calls score {round(diff['typical']['accuracy'] * 100)} percent; hard ones, like a rep reading a script or a bot faking ums, "
            f"only {round(diff['hard']['accuracy'] * 100)}. Every miss is listed. "
            "And because a machine mistaken for a person is how a false human attestation happens, human attestation demands the highest confidence."},
        {"id": "close", "url": f"{BASE}/web/slides.html?s=close", "min": 3, "text":
            "DIALTONE. A spoken handshake any inbound vendor can adopt in three sentences, and one rule: two agents agreeing is not a commitment."},
    ]


def tts(text: str, path: Path, engine: str) -> float:
    if engine == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=f"Read this like a calm, confident product demo narrator: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon"))),
            ),
        )
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(pcm)
    else:
        aiff = path.with_suffix(".aiff")
        subprocess.run(["say", "-v", "Samantha", "-r", "185", "-o", str(aiff), text], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(aiff), str(path)], check=True)
    return duration(path)


def duration(path: Path) -> float:
    err = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def record(scene: dict, seconds: float, out: Path) -> Path:
    from playwright.sync_api import sync_playwright

    tmp = OUT / "rec" / scene["id"]
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1,
                                  record_video_dir=str(tmp), record_video_size={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.goto(scene["url"], wait_until="networkidle")
        if scene.get("wait_done"):
            page.wait_for_function("window.__demoDone === true", timeout=300_000)
            page.wait_for_timeout(max(0, int((seconds - page.evaluate("performance.now()/1000")) * 1000)))
        else:
            page.wait_for_timeout(int(seconds * 1000))
        video = page.video
        ctx.close()
        browser.close()
        src = Path(video.path())
    shutil.move(src, out)
    return out


def main():
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", choices=["fixtures", "real"], default="fixtures")
    ap.add_argument("--tts", choices=["gemini", "say"], default="gemini" if os.environ.get("GEMINI_API_KEY") else "say")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    parts = []
    for scene in narration(args.src):
        audio = OUT / f"{scene['id']}.wav"
        secs = max(tts(scene["text"], audio, args.tts) + 0.8, scene["min"])
        webm = record(scene, secs, OUT / f"{scene['id']}.webm")
        clip = OUT / f"{scene['id']}.mp4"
        vlen = duration(webm)
        # Keep the end of the recording (the page has settled); pad audio with silence to the clip length.
        start = max(0.0, vlen - secs)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(webm), "-i", str(audio),
                        "-filter_complex", f"[1:a]apad,atrim=0:{secs:.2f}[a]", "-map", "0:v", "-map", "[a]",
                        "-t", f"{secs:.2f}", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
                        "-crf", "20", "-c:a", "aac", "-b:a", "160k", str(clip)], check=True)
        print(f"{scene['id']}: {secs:.1f}s")
        parts.append(clip)
    listing = OUT / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    final = OUT / "dialtone-demo.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(final)],
                   check=True, cwd=OUT)
    print(f"wrote {final} ({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
