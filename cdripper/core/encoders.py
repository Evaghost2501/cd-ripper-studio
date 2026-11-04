"""Audio encoders. Input is raw CD PCM (44.1 kHz, 16-bit, stereo, little-endian)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass

import numpy as np


@dataclass
class FormatInfo:
    key: str
    name: str
    ext: str
    lossless: bool
    qualities: list[tuple[str, str]]     # (key, label)
    default: str
    needs_ffmpeg: bool = False
    note: str = ""


FORMATS = [
    FormatInfo("mp3", "MP3 (LAME)", ".mp3", False,
               [("cbr320", "CBR 320 kbps (최고 음질, 호환성 최고)"),
                ("v0", "VBR V0 (~245 kbps, 투명 음질·작은 용량)"),
                ("v2", "VBR V2 (~190 kbps)"),
                ("cbr256", "CBR 256 kbps"),
                ("cbr192", "CBR 192 kbps")], "cbr320",
               note="어디서나 재생됩니다. 320 kbps 또는 V0 권장."),
    FormatInfo("flac", "FLAC (무손실)", ".flac", True,
               [("8", "압축 레벨 8 (최대 압축)"), ("5", "압축 레벨 5 (기본)"), ("0", "압축 레벨 0 (빠름)")], "8",
               note="CD와 비트 단위로 동일. 보관용으로 강력 추천 (약 50~60% 용량)."),
    FormatInfo("alac", "ALAC (Apple 무손실, .m4a)", ".m4a", True, [("default", "기본")], "default",
               needs_ffmpeg=True, note="iPhone / Apple Music 사용자를 위한 무손실."),
    FormatInfo("aac", "AAC (.m4a)", ".m4a", False,
               [("256", "256 kbps"), ("320", "320 kbps"), ("192", "192 kbps")], "256",
               needs_ffmpeg=True, note="같은 비트레이트에서 MP3보다 음질 우수, Apple 기기 친화."),
    FormatInfo("opus", "Opus (.opus)", ".opus", False,
               [("0.9", "고음질 (~192 kbps)"), ("0.7", "표준 (~128 kbps)")], "0.9",
               note="현존 최고 효율 손실 코덱. 일부 구형 기기 미지원."),
    FormatInfo("wav", "WAV (비압축)", ".wav", True, [("default", "16-bit / 44.1 kHz")], "default",
               note="태그 지원이 제한적. 편집용."),
]
FORMAT_BY_KEY = {f.key: f for f in FORMATS}


def find_ffmpeg(configured: str = "") -> str | None:
    cands = [configured] if configured else []
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    cands += [os.path.join(base, "ffmpeg.exe"), os.path.join(base, "ffmpeg"), shutil.which("ffmpeg") or ""]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def encode(pcm: bytes, fmt: str, quality: str, path: str, ffmpeg: str | None = None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    if fmt == "mp3":
        _mp3(pcm, quality, tmp)
    elif fmt == "flac":
        _soundfile(pcm, tmp, "FLAC", "PCM_16", compression=int(quality) / 8)
    elif fmt == "opus":
        _soundfile(pcm, tmp, "OGG", "OPUS", compression=1 - float(quality))
    elif fmt == "wav":
        with wave.open(tmp, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes(pcm)
    elif fmt in ("aac", "alac"):
        if not ffmpeg:
            raise RuntimeError("이 포맷은 ffmpeg.exe가 필요합니다. 설정에서 경로를 지정하세요.")
        args = ["-c:a", "alac"] if fmt == "alac" else ["-c:a", "aac", "-b:a", f"{quality}k"]
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", "44100",
               "-ac", "2", "-i", "pipe:0", *args, "-f", "ipod", tmp]
        flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
        p = subprocess.run(cmd, input=pcm, capture_output=True, creationflags=flags)
        if p.returncode:
            raise RuntimeError("ffmpeg 오류: " + p.stderr.decode(errors="replace")[-500:])
    else:
        raise ValueError(fmt)
    os.replace(tmp, path)


def _mp3(pcm: bytes, quality: str, path: str):
    import lameenc
    enc = lameenc.Encoder()
    enc.set_in_sample_rate(44100)
    enc.set_channels(2)
    enc.set_quality(0)            # best (slowest) algorithm
    if quality.startswith("v"):
        enc.set_vbr(4)            # vbr_mtrh (LAME default VBR mode)
        enc.set_vbr_quality(int(quality[1:]))
    else:
        enc.set_bit_rate(int(quality[3:]))
    data = enc.encode(pcm) + enc.flush()
    if quality.startswith("v"):
        from .xing import add_xing_header
        data = add_xing_header(data)
    with open(path, "wb") as f:
        f.write(data)


def _soundfile(pcm: bytes, path: str, fmt: str, subtype: str, compression: float):
    import soundfile as sf
    arr = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)
    rate = 44100
    if subtype == "OPUS":   # Opus only runs at 48 kHz
        import soxr
        arr = soxr.resample(arr.astype(np.float32) / 32768, 44100, 48000, quality="VHQ")
        rate = 48000
    with sf.SoundFile(path, "w", rate, 2, subtype=subtype, format=fmt,
                      compression_level=max(0.0, min(1.0, compression))) as f:
        f.write(arr)
