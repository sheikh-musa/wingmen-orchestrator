from __future__ import annotations

import glob
import os
import subprocess

from legacy.reel_triage import config


def build_ytdlp_cmd(url: str, out_path: str) -> list[str]:
    # Public fetch ONLY — no cookies, ever (v1 ban-risk hygiene + zero IG creds).
    return ["yt-dlp", "--no-playlist", "-f", "mp4", "-o", out_path, url]


def build_keyframe_cmd(media: str, frames_dir: str,
                       max_frames: int = config.MAX_KEYFRAMES) -> list[str]:
    # scene-detect, cap frames
    return ["ffmpeg", "-i", media,
            "-vf", "select='gt(scene,0.3)',showinfo",
            "-frames:v", str(max_frames), "-vsync", "vfr",
            os.path.join(frames_dir, "frame_%02d.jpg")]


def fetch(url: str, work_dir: str) -> str:
    media = os.path.join(work_dir, "reel.mp4")
    subprocess.run(build_ytdlp_cmd(url, media), capture_output=True, text=True,
                   check=True)
    return media


def keyframes(media: str, frames_dir: str) -> list[str]:
    os.makedirs(frames_dir, exist_ok=True)
    subprocess.run(build_keyframe_cmd(media, frames_dir), capture_output=True,
                   text=True, check=True)
    return sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))


def cleanup_media(media: str) -> None:
    # Media never persists (Amanah/Satr). Transcript is the only retained artifact.
    if os.path.exists(media):
        os.remove(media)
