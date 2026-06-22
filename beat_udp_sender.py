import json
import math
import os
import signal
import socket
import time
from collections import deque

import librosa
import numpy as np
import pyaudiowpatch as pyaudio

try:
    import msvcrt
except ImportError:
    msvcrt = None


# ============================================================
# UDP target: Windows -> Ubuntu WSL receiver
# ============================================================

UDP_HOST = os.environ.get("GO2_BEAT_HOST", "127.0.0.1")
UDP_PORT = int(os.environ.get("GO2_BEAT_PORT", "5005"))

# Use "plain" if your Ubuntu receiver expects only: NOD
# Use "json" if your receiver parses JSON.
UDP_PAYLOAD_MODE = os.environ.get("GO2_PAYLOAD_MODE", "plain").lower()


# ============================================================
# Audio capture
# ============================================================

CHUNK = 2048


# ============================================================
# Beat analysis settings
# ============================================================

ANALYSIS_SR = 22050
ANALYSIS_SECONDS = 10.0
ANALYSIS_INTERVAL = 0.75
HOP_LENGTH = 512

TEMPO_SMOOTHING = 0.18
MIN_BEATS_TO_LOCK = 4


# ============================================================
# Robot-safe tempo settings
# ============================================================

# Main robot tempo limit.
# Example:
# music = 112 BPM
# MAX_ROBOT_BPM = 90
# divisor = ceil(112 / 90) = 2
# dog tempo = 56 BPM
MAX_ROBOT_BPM = float(os.environ.get("GO2_MAX_ROBOT_BPM", "90"))

# Optional manual divisor.
# 0 = automatic half-time limiter.
# Use 2 for forced half-time.
# Use 4 for forced quarter-time.
# Do NOT use 3 just because the song is 3/4.
# Divisor 3 or 5 should only be used for special bar-accent gestures,
# not for normal tempo slowing.# Optional manual divisor.
# 0 = automatic.
# For 3/4 songs, you can try 3 if you want one nod per bar.
FORCE_DIVISOR = int(os.environ.get("GO2_FORCE_DIVISOR", "0"))

# Phase shift in raw music beats.
# -1.0 usually shifts nods from 2/4 to 1/3.
#  0.0 means no timing shift.
# +1.0 shifts later by one raw beat.
ROBOT_PHASE_BEATS = float(os.environ.get("GO2_PHASE_BEATS", "-1.0"))

# How strongly the scheduled nod timing corrects toward new detected beat phase.
PHASE_CORRECTION = 0.25


# ============================================================
# Stop handling
# ============================================================

STOP_REQUESTED = False


def request_stop(signum=None, frame=None):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print("\n[stop] requested, closing cleanly...", flush=True)


signal.signal(signal.SIGINT, request_stop)
signal.signal(signal.SIGTERM, request_stop)


def check_keyboard_stop():
    global STOP_REQUESTED

    if msvcrt is None:
        return STOP_REQUESTED

    if msvcrt.kbhit():
        key = msvcrt.getwch().lower()

        if key == "q":
            request_stop()

        # Ctrl+C character fallback.
        if key == "\x03":
            request_stop()

    return STOP_REQUESTED


# ============================================================
# Audio helpers
# ============================================================

def to_mono_float32(samples: np.ndarray, channels: int) -> np.ndarray:
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32)


def safe_scalar(value):
    arr = np.asarray(value).reshape(-1)
    if len(arr) == 0:
        return None
    return float(arr[0])


# ============================================================
# Robot BPM limiter
# ============================================================

def make_robot_safe_bpm(raw_bpm: float, fallback_bpm: float = None):
    if raw_bpm is None:
        raw_bpm = fallback_bpm

    if raw_bpm is None:
        return None, 1

    raw_bpm = float(raw_bpm)

    if not np.isfinite(raw_bpm) or raw_bpm <= 0:
        return None, 1

    if FORCE_DIVISOR > 0:
        divisor = FORCE_DIVISOR
    else:
        # Musical half-time rule:
        # Only divide by powers of 2: 1, 2, 4, 8...
        # Do not divide by 3 just because the song is 3/4.
        divisor = 1

        while raw_bpm / divisor > MAX_ROBOT_BPM:
            divisor *= 2

    dog_bpm = raw_bpm / divisor

    return dog_bpm, divisor


# ============================================================
# Beat analysis
# ============================================================

def analyze_beats(audio: np.ndarray, sr: int):
    """
    Returns:
        raw_bpm, last_beat_time_inside_window

    If beat detection is not reliable yet, returns:
        None, None
    """

    if len(audio) < sr * 4:
        return None, None

    if sr != ANALYSIS_SR:
        audio = librosa.resample(
            y=audio,
            orig_sr=sr,
            target_sr=ANALYSIS_SR,
        )
        sr = ANALYSIS_SR

    audio = audio - np.mean(audio)
    peak = np.max(np.abs(audio))

    if peak < 0.005:
        return None, None

    audio = audio / max(peak, 1e-6)

    tempo, beat_times = librosa.beat.beat_track(
        y=audio,
        sr=sr,
        hop_length=HOP_LENGTH,
        start_bpm=110,
        tightness=120,
        trim=True,
        units="time",
    )

    tempo = safe_scalar(tempo)

    if tempo is None or len(beat_times) < MIN_BEATS_TO_LOCK:
        return None, None

    recent_beats = np.asarray(beat_times[-8:], dtype=float)

    intervals = np.diff(recent_beats)
    intervals = intervals[(intervals > 0.25) & (intervals < 1.4)]

    if len(intervals) >= 3:
        raw_bpm = 60.0 / float(np.median(intervals))
    else:
        raw_bpm = tempo

    if raw_bpm is None or not np.isfinite(raw_bpm) or raw_bpm <= 0:
        return None, None

    last_beat_time = float(recent_beats[-1])

    return raw_bpm, last_beat_time


# ============================================================
# UDP sender
# ============================================================

def send_nod(sock, raw_bpm: float, dog_bpm: float, divisor: int):
    if UDP_PAYLOAD_MODE == "json":
        msg = {
            "event": "NOD",
            "raw_bpm": float(raw_bpm),
            "dog_bpm": float(dog_bpm),
            "divisor": int(divisor),
            "phase_beats": float(ROBOT_PHASE_BEATS),
            "time": time.time(),
        }

        payload = json.dumps(msg).encode("utf-8")
    else:
        payload = b"NOD"

    sock.sendto(payload, (UDP_HOST, UDP_PORT))


# ============================================================
# Main loop
# ============================================================

def main():
    global STOP_REQUESTED

    print("GO2 Librosa Beat UDP Sender")
    print(f"Sending NOD messages to {UDP_HOST}:{UDP_PORT}")
    print(f"Payload mode: {UDP_PAYLOAD_MODE}")
    print(f"Robot max BPM: {MAX_ROBOT_BPM:.1f}")
    print(f"Force divisor: {FORCE_DIVISOR if FORCE_DIVISOR > 0 else 'auto'}")
    print(f"Phase shift: {ROBOT_PHASE_BEATS:+.1f} raw beat")
    print()
    print("Controls:")
    print("  Press q to stop")
    print("  Ctrl+C should also stop")
    print()
    print("Play music on your computer. Wait 5-10 seconds for lock.")
    print()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    audio_buffer = deque()
    buffer_sample_count = 0

    locked_dog_bpm = None
    locked_period = None
    next_nod_time = None

    last_analysis_time = 0.0

    # Fix for None BPM crash:
    # Store the last valid music BPM.
    last_raw_bpm = None
    last_divisor = 1

    try:
        with pyaudio.PyAudio() as p:
            loopback_device = p.get_default_wasapi_loopback()

            rate = int(loopback_device["defaultSampleRate"])
            channels = int(loopback_device.get("maxInputChannels", 2)) or 2
            max_samples = int(rate * ANALYSIS_SECONDS)

            print("Using loopback device:")
            print(f"  Name: {loopback_device['name']}")
            print(f"  Sample rate: {rate}")
            print(f"  Channels: {channels}")
            print()

            with p.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=CHUNK,
            ) as stream:

                while not STOP_REQUESTED:
                    check_keyboard_stop()

                    if STOP_REQUESTED:
                        break

                    now = time.time()

                    data = stream.read(
                        CHUNK,
                        exception_on_overflow=False,
                    )

                    raw = np.frombuffer(data, dtype=np.float32)
                    mono = to_mono_float32(raw, channels)

                    audio_buffer.append(mono)
                    buffer_sample_count += len(mono)

                    while buffer_sample_count > max_samples:
                        removed = audio_buffer.popleft()
                        buffer_sample_count -= len(removed)

                    # --------------------------------------------------------
                    # Re-analyze beat timing every ANALYSIS_INTERVAL seconds
                    # --------------------------------------------------------

                    if now - last_analysis_time >= ANALYSIS_INTERVAL:
                        last_analysis_time = now

                        if len(audio_buffer) == 0:
                            continue

                        audio = np.concatenate(list(audio_buffer))

                        raw_bpm, last_beat_in_window = analyze_beats(
                            audio,
                            rate,
                        )

                        if raw_bpm is not None and last_beat_in_window is not None:
                            dog_bpm, divisor = make_robot_safe_bpm(
                                raw_bpm,
                                fallback_bpm=last_raw_bpm,
                            )

                            if dog_bpm is None:
                                continue

                            last_raw_bpm = raw_bpm
                            last_divisor = divisor

                            if locked_dog_bpm is None:
                                locked_dog_bpm = dog_bpm
                            else:
                                locked_dog_bpm = (
                                    locked_dog_bpm * (1.0 - TEMPO_SMOOTHING)
                                    + dog_bpm * TEMPO_SMOOTHING
                                )

                            locked_period = 60.0 / locked_dog_bpm

                            buffer_duration = len(audio) / rate
                            buffer_start_time = now - buffer_duration

                            last_beat_abs = buffer_start_time + last_beat_in_window

                            # ------------------------------------------------
                            # Phase fix:
                            # Shift from detected beat timing by raw music beat.
                            # Example:
                            #   ROBOT_PHASE_BEATS = -1.0
                            #   shifts 2/4 feeling toward 1/3 feeling.
                            # ------------------------------------------------

                            raw_period = 60.0 / raw_bpm

                            candidate_next = last_beat_abs + (
                                ROBOT_PHASE_BEATS * raw_period
                            )

                            while candidate_next <= now:
                                candidate_next += locked_period

                            if next_nod_time is None:
                                next_nod_time = candidate_next

                                print(
                                    f"LOCKED | music≈{raw_bpm:.1f} BPM | "
                                    f"/{divisor} | dog≈{locked_dog_bpm:.1f} BPM | "
                                    f"phase={ROBOT_PHASE_BEATS:+.1f}",
                                    flush=True,
                                )
                            else:
                                phase_error = candidate_next - next_nod_time

                                if abs(phase_error) < locked_period * 0.40:
                                    next_nod_time += phase_error * PHASE_CORRECTION

                    # --------------------------------------------------------
                    # Send NOD events according to robot-safe schedule
                    # --------------------------------------------------------

                    if locked_period is not None and next_nod_time is not None:
                        if now >= next_nod_time:
                            current_raw = last_raw_bpm

                            if current_raw is None or locked_dog_bpm is None:
                                continue

                            current_dog, current_divisor = make_robot_safe_bpm(
                                current_raw,
                                fallback_bpm=locked_dog_bpm,
                            )

                            if current_dog is None:
                                continue

                            print(
                                f"SEND NOD | music≈{current_raw:.1f} BPM | "
                                f"/{current_divisor} | "
                                f"dog≈{locked_dog_bpm:.1f} BPM | "
                                f"phase={ROBOT_PHASE_BEATS:+.1f}",
                                flush=True,
                            )

                            send_nod(
                                sock,
                                raw_bpm=current_raw,
                                dog_bpm=locked_dog_bpm,
                                divisor=current_divisor,
                            )

                            while next_nod_time <= now:
                                next_nod_time += locked_period

    finally:
        sock.close()
        print("[done] sender closed cleanly.")


if __name__ == "__main__":
    main()