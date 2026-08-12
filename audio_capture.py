"""Non-exclusive WASAPI loopback + microphone capture, so a recording can run alongside an
active Teams/Zoom call without disturbing it. pyaudiowpatch (the WASAPI-patched PyAudio fork)
opens streams in shared mode by default — the same mode the call software itself uses — so
both this capture and Teams/Zoom can read the mic concurrently; nothing here takes exclusive
ownership of any device.

Loopback (everyone else's audio, played through the speakers) and mic (the user's own voice)
come in as two separate streams at two different sample rates — the loopback device is locked
to Windows' current output mix format (commonly 48kHz), independent of whatever rate the mic
negotiates — so they're resampled to a common rate and averaged into one mono track before
being handed to transcribe.py.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
from scipy.signal import resample_poly

CHUNK = 1024
FORMAT = pyaudio.paInt16
SAMPLE_WIDTH = 2  # bytes — matches paInt16


class AudioCapture:
    """One capture session: start() to begin, stop(out_path) to flush a single mixed WAV."""

    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._loop_stream = None
        self._mic_stream = None
        self._loop_frames: list[bytes] = []
        self._mic_frames: list[bytes] = []
        self._loop_rate: int | None = None
        self._mic_rate: int | None = None
        self._loop_channels: int | None = None
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def _get_loopback_device(self) -> dict:
        wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = self._pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        if default_speakers.get("isLoopbackDevice"):
            return default_speakers
        for loopback in self._pa.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                return loopback
        raise RuntimeError("No WASAPI loopback device found matching the default output device")

    def _get_default_mic_device(self) -> dict:
        wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        return self._pa.get_device_info_by_index(wasapi_info["defaultInputDevice"])

    def _record_loop(self, stream, frames: list[bytes]) -> None:
        while not self._stop_event.is_set():
            try:
                frames.append(stream.read(CHUNK, exception_on_overflow=False))
            except Exception:
                break

    def start(self) -> None:
        loop_device = self._get_loopback_device()
        mic_device = self._get_default_mic_device()

        self._loop_rate = int(loop_device["defaultSampleRate"])
        self._loop_channels = int(loop_device["maxInputChannels"]) or 2
        self._mic_rate = int(mic_device["defaultSampleRate"])

        self._loop_stream = self._pa.open(
            format=FORMAT, channels=self._loop_channels, rate=self._loop_rate,
            input=True, input_device_index=loop_device["index"], frames_per_buffer=CHUNK,
        )
        self._mic_stream = self._pa.open(
            format=FORMAT, channels=1, rate=self._mic_rate,
            input=True, input_device_index=mic_device["index"], frames_per_buffer=CHUNK,
        )

        self._stop_event.clear()
        for stream, frames in ((self._loop_stream, self._loop_frames), (self._mic_stream, self._mic_frames)):
            t = threading.Thread(target=self._record_loop, args=(stream, frames), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, out_path: str | Path) -> Path:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)
        for stream in (self._loop_stream, self._mic_stream):
            if stream is not None:
                stream.stop_stream()
                stream.close()
        self._pa.terminate()

        mixed = self._mix()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(self._loop_rate)
            wf.writeframes(mixed.tobytes())
        return out_path

    def _mix(self) -> np.ndarray:
        loop_bytes = b"".join(self._loop_frames)
        mic_bytes = b"".join(self._mic_frames)

        loop_arr = np.frombuffer(loop_bytes, dtype=np.int16).astype(np.float32)
        if self._loop_channels and self._loop_channels > 1:
            # audioop (the stdlib's old tomono helper) was removed in Python 3.13 — trim to a
            # whole number of frames, reshape to (frames, channels), and average across
            # channels ourselves for the same stereo/multichannel -> mono downmix.
            usable = loop_arr.size - (loop_arr.size % self._loop_channels)
            loop_arr = loop_arr[:usable].reshape(-1, self._loop_channels).mean(axis=1)

        mic_arr = np.frombuffer(mic_bytes, dtype=np.int16).astype(np.float32)

        if mic_arr.size and self._mic_rate != self._loop_rate:
            mic_arr = resample_poly(mic_arr, self._loop_rate, self._mic_rate)

        n = max(loop_arr.size, mic_arr.size)
        loop_arr = np.pad(loop_arr, (0, n - loop_arr.size))
        mic_arr = np.pad(mic_arr, (0, n - mic_arr.size))

        return np.clip((loop_arr + mic_arr) / 2, -32768, 32767).astype(np.int16)
