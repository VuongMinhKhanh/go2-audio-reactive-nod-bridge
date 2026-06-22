#!/usr/bin/env python3
r"""
go2_audio_nod_bridge_final_v1.py

Final audio-reactive Go2 nod bridge.

Works in two modes:

1) Pure Python / Windows-safe fake mode:
   GO2_BACKEND=fake
   - No Ubuntu
   - No Unitree SDK
   - No CycloneDDS
   - Prints the same BalanceStand / Euler / StopMove commands
   - Good for testing beat detection and nod timing on another laptop

2) Future real SDK mode:
   GO2_BACKEND=sdk
   - Requires unitree_sdk2_python + CycloneDDS
   - Uses SportClient.BalanceStand(), SportClient.Euler(), SportClient.StopMove()
   - Supports send-only mode for dry/pseudo robot testing

Recommended Windows fake-mode run:

PowerShell terminal 1:
    cd C:\go2_audio_reactive_sim
    .\.venv\Scripts\activate

    $env:GO2_BACKEND="fake"
    $env:GO2_SEND_ONLY="1"
    $env:GO2_BEAT_PORT="5005"
    $env:GO2_MAX_PITCH_RAD="0.025"
    $env:GO2_SEND_HZ="12"
    $env:GO2_PRINT_RAW="0"

    python go2_audio_nod_bridge_final_v1.py

PowerShell terminal 2:
    cd C:\go2_audio_reactive_sim
    .\.venv\Scripts\activate

    $env:GO2_BEAT_HOST="127.0.0.1"
    $env:GO2_BEAT_PORT="5005"
    $env:GO2_PAYLOAD_MODE="phase"
    $env:GO2_MAX_ROBOT_BPM="120"
    $env:GO2_PHASE_BEATS="1"

    python beat_udp_sender.py
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


# -----------------------------
# Environment helpers
# -----------------------------

def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[warn] invalid {name}={raw!r}; using {default}")
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[warn] invalid {name}={raw!r}; using {default}")
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# -----------------------------
# Optional low-level stdout/stderr suppression
# -----------------------------

@contextlib.contextmanager
def suppress_fd_output(enabled: bool):
    """
    Suppress noisy SDK output, including some native/C-level writes.

    This is intentionally used only around SDK calls in GO2_SEND_ONLY=1.
    Fake backend never needs it.
    """
    if not enabled:
        yield
        return

    sys.stdout.flush()
    sys.stderr.flush()

    old_stdout_fd = None
    old_stderr_fd = None
    devnull_fd = None

    try:
        old_stdout_fd = os.dup(1)
        old_stderr_fd = os.dup(2)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)

        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)

        yield

    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        if old_stdout_fd is not None:
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)

        if old_stderr_fd is not None:
            os.dup2(old_stderr_fd, 2)
            os.close(old_stderr_fd)

        if devnull_fd is not None:
            os.close(devnull_fd)


# -----------------------------
# Config
# -----------------------------

@dataclass
class Config:
    backend: str
    send_only: bool
    iface: str
    beat_host: str
    beat_port: int
    max_pitch_rad: float
    send_hz: float
    sdk_timeout: float
    print_raw: bool
    motion_mode: str
    beat_decay_sec: float
    min_strength: float
    phase_smooth_alpha: float
    real_require_ok: bool

    @classmethod
    def from_env_and_args(cls) -> "Config":
        # CLI iface keeps compatibility with old Ubuntu command:
        # python3 go2_audio_nod_bridge_final_v1.py eth0
        cli_iface = sys.argv[1] if len(sys.argv) >= 2 else ""

        backend = env_str("GO2_BACKEND", "fake").lower()
        if backend not in ("fake", "sdk", "auto"):
            print(f"[warn] invalid GO2_BACKEND={backend!r}; using fake")
            backend = "fake"

        send_only = env_bool("GO2_SEND_ONLY", True)

        return cls(
            backend=backend,
            send_only=send_only,
            iface=env_str("GO2_IFACE", cli_iface or "eth0"),
            beat_host=env_str("GO2_BIND_HOST", "0.0.0.0"),
            beat_port=env_int("GO2_BEAT_PORT", 5005),
            max_pitch_rad=env_float("GO2_MAX_PITCH_RAD", 0.025),
            send_hz=max(1.0, env_float("GO2_SEND_HZ", 12.0)),
            sdk_timeout=env_float("GO2_SDK_TIMEOUT", 0.03),
            print_raw=env_bool("GO2_PRINT_RAW", False),
            motion_mode=env_str("GO2_MOTION_MODE", "nod").lower(),
            beat_decay_sec=max(0.05, env_float("GO2_BEAT_DECAY_SEC", 0.45)),
            min_strength=clamp(env_float("GO2_MIN_STRENGTH", 0.0), 0.0, 1.0),
            phase_smooth_alpha=clamp(env_float("GO2_PHASE_SMOOTH_ALPHA", 0.35), 0.0, 1.0),
            real_require_ok=not send_only,
        )


# -----------------------------
# Robot backends
# -----------------------------

class FakeSportBackend:
    """
    Pure Python backend. Safe on Windows and on laptops without Ubuntu/SDK.

    Return value convention follows SportClient style: 0 means OK.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def init(self) -> None:
        print("[backend] fake Pure-Python SportClient")
        print("[backend] no Unitree SDK, no CycloneDDS, no robot required")

    def BalanceStand(self) -> int:
        print("[CMD] BalanceStand()")
        return 0

    def Euler(self, roll: float, pitch: float, yaw: float) -> int:
        print(f"[CMD] Euler(roll={roll:+.3f}, pitch={pitch:+.4f}, yaw={yaw:+.3f})")
        return 0

    def StopMove(self) -> int:
        print("[CMD] StopMove()")
        return 0


class SDKSportBackend:
    """
    Real Unitree SDK backend.

    Intended for Ubuntu/WSL now, and potentially Windows later if:
    - unitree_sdk2_python is installed
    - CycloneDDS works
    - Python version is compatible
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sport = None

    def init(self) -> None:
        print("[backend] sdk Unitree SportClient")
        print(f"[sdk] iface={self.cfg.iface} timeout={self.cfg.sdk_timeout}")

        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.go2.sport.sport_client import SportClient
        except Exception as e:
            raise RuntimeError(
                "Cannot import unitree_sdk2py. Use GO2_BACKEND=fake for Windows-only testing, "
                "or install unitree_sdk2_python + CycloneDDS for SDK mode. "
                f"Import error: {e}"
            ) from e

        # Most Unitree examples use:
        # ChannelFactoryInitialize(0, "eth0")
        # Some local/test setups may work with an empty iface, but our checkpoint uses eth0.
        with suppress_fd_output(self.cfg.send_only):
            ChannelFactoryInitialize(0, self.cfg.iface)
            self.sport = SportClient()
            try:
                self.sport.SetTimeout(self.cfg.sdk_timeout)
            except Exception:
                # Older versions may not expose SetTimeout consistently.
                pass
            self.sport.Init()

    def _call(self, name: str, *args: Any) -> int:
        if self.sport is None:
            raise RuntimeError("SDK backend was not initialized")

        fn = getattr(self.sport, name)

        try:
            with suppress_fd_output(self.cfg.send_only):
                ret = fn(*args)
        except Exception as e:
            if self.cfg.send_only:
                # Pretend-real/send-only mode: do not crash on missing robot.
                return 3102
            raise RuntimeError(f"SportClient.{name} failed: {e}") from e

        if ret is None:
            ret = 0

        try:
            ret_i = int(ret)
        except Exception:
            ret_i = -9999

        if self.cfg.real_require_ok and ret_i != 0:
            print(f"[sdk-error] SportClient.{name} returned ret={ret_i}")

        return ret_i

    def BalanceStand(self) -> int:
        print("[CMD] BalanceStand()")
        return self._call("BalanceStand")

    def Euler(self, roll: float, pitch: float, yaw: float) -> int:
        print(f"[CMD] Euler(roll={roll:+.3f}, pitch={pitch:+.4f}, yaw={yaw:+.3f})")
        return self._call("Euler", roll, pitch, yaw)

    def StopMove(self) -> int:
        print("[CMD] StopMove()")
        return self._call("StopMove")


def build_backend(cfg: Config):
    if cfg.backend == "fake":
        robot = FakeSportBackend(cfg)
        robot.init()
        return robot

    if cfg.backend == "sdk":
        robot = SDKSportBackend(cfg)
        robot.init()
        return robot

    # auto mode:
    # Try SDK first. If it fails and GO2_SEND_ONLY=1, fall back to fake.
    try:
        robot = SDKSportBackend(cfg)
        robot.init()
        return robot
    except Exception as e:
        if not cfg.send_only:
            raise
        print(f"[backend] SDK unavailable; falling back to fake because GO2_SEND_ONLY=1")
        print(f"[backend] SDK reason: {e}")
        robot = FakeSportBackend(cfg)
        robot.init()
        return robot


# -----------------------------
# UDP payload parsing
# -----------------------------

@dataclass
class BeatState:
    # Last source values
    bpm: float = 90.0
    strength: float = 0.0
    phase: Optional[float] = None

    # Beat-event envelope
    last_beat_time: float = 0.0
    beat_count: int = 0
    beat_impulse_strength: float = 0.0

    # Smoothed output
    pitch: float = 0.0
    last_packet_time: float = 0.0


def parse_payload(raw: bytes) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Accepts:
    - JSON dict:
      {"beat":1,"strength":0.75,"bpm":90}
      {"phase":0.25,"strength":0.7,"bpm":90}
    - Simple key=value strings:
      beat=1 strength=0.75 bpm=90
      phase=0.25,strength=0.7,bpm=90
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None, text

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, text
        return None, text
    except json.JSONDecodeError:
        pass

    # Fallback key=value parser.
    data: Dict[str, Any] = {}
    cleaned = text.replace(",", " ")
    for part in cleaned.split():
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        try:
            if "." in v:
                data[k] = float(v)
            else:
                data[k] = int(v)
        except ValueError:
            data[k] = v

    if data:
        return data, text

    return None, text


def float_from(data: Dict[str, Any], key: str, default: float) -> float:
    try:
        val = data.get(key, default)
        if val is None:
            return default
        return float(val)
    except Exception:
        return default


def update_state_from_packet(cfg: Config, state: BeatState, data: Dict[str, Any], raw_text: str) -> None:
    now = time.monotonic()
    state.last_packet_time = now

    bpm = float_from(data, "bpm", state.bpm)
    if bpm > 0:
        state.bpm = bpm

    strength = float_from(data, "strength", state.strength)
    strength = clamp(strength, 0.0, 1.0)
    if strength < cfg.min_strength:
        strength = 0.0
    state.strength = strength

    if cfg.print_raw:
        print(f"[RAW] {raw_text}")

    # Phase packet from beat_udp_sender.py:
    # {"phase":0.25,"strength":0.7,"bpm":90}
    if "phase" in data:
        phase = float_from(data, "phase", 0.0)
        # Normalize phase to [0, 1).
        phase = phase % 1.0
        state.phase = phase
        print(f"[PHASE] bpm={state.bpm:.1f} strength={state.strength:.2f} phase={phase:.3f}")
        return

    # Beat-event packet from fake PowerShell test:
    # {"beat":1,"strength":0.75,"bpm":90}
    beat_value = data.get("beat", 0)
    is_beat = False
    try:
        is_beat = float(beat_value) > 0
    except Exception:
        is_beat = str(beat_value).lower() in ("true", "yes", "on")

    if is_beat:
        state.beat_count += 1
        state.last_beat_time = now
        state.beat_impulse_strength = state.strength
        # When event packets arrive, disable phase until a phase packet returns.
        state.phase = None
        print(f"[BEAT #{state.beat_count:04d}] bpm={state.bpm:.1f} strength={state.strength:.2f}")


# -----------------------------
# Pitch calculation
# -----------------------------

def calculate_pitch(cfg: Config, state: BeatState, now: float) -> float:
    """
    NOD motion only.

    Sign convention:
    - Negative pitch = forward/down nod target
    - 0 = neutral

    For phase packets:
    - Use a smooth one-cycle nod wave
    - phase=0.0 starts strongest downward nod
    - phase=0.5 returns close to neutral
    - strength scales amplitude

    For beat-event packets:
    - Each beat creates a downward impulse
    - It decays back to neutral over GO2_BEAT_DECAY_SEC
    """
    amp = cfg.max_pitch_rad * clamp(state.strength, 0.0, 1.0)

    if cfg.motion_mode != "nod":
        # Future-proof, but intentionally safe.
        return 0.0

    if state.phase is not None:
        # Smooth down-and-return gesture:
        # phase=0.0 -> -amp
        # phase=0.5 -> 0
        # phase=1.0 -> -amp again
        # This gives a continuous nod instead of sharp snapping.
        wave = 0.5 * (1.0 + math.cos(2.0 * math.pi * state.phase))
        target = -amp * wave

        # Light smoothing avoids jitter from phase detector fluctuations.
        alpha = cfg.phase_smooth_alpha
        if alpha <= 0:
            return target
        if alpha >= 1:
            state.pitch = target
            return state.pitch

        state.pitch = (alpha * target) + ((1.0 - alpha) * state.pitch)
        return state.pitch

    # Beat-event envelope.
    if state.last_beat_time <= 0:
        target = 0.0
    else:
        age = max(0.0, now - state.last_beat_time)
        if age >= cfg.beat_decay_sec:
            target = 0.0
        else:
            # Exponential decay: starts at -maxPitch*strength, returns to 0.
            decay = math.exp(-4.0 * age / cfg.beat_decay_sec)
            target = -cfg.max_pitch_rad * state.beat_impulse_strength * decay

    # Smooth event mode a little too.
    state.pitch = (0.45 * target) + (0.55 * state.pitch)
    if abs(state.pitch) < 1e-5:
        state.pitch = 0.0
    return state.pitch


# -----------------------------
# Main loop
# -----------------------------

def make_udp_socket(cfg: Config) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Useful during restarts on Windows/macOS/Linux.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass

    sock.bind((cfg.beat_host, cfg.beat_port))
    sock.setblocking(False)
    return sock


def print_startup(cfg: Config) -> None:
    print("")
    print("=== GO2 AUDIO NOD BRIDGE FINAL V1 ===")
    print(f"backend          : {cfg.backend}")
    print(f"send_only        : {int(cfg.send_only)}")
    print(f"motion_mode      : {cfg.motion_mode}")
    print(f"bind             : {cfg.beat_host}:{cfg.beat_port}")
    print(f"max_pitch_rad    : {cfg.max_pitch_rad}")
    print(f"send_hz          : {cfg.send_hz}")
    print(f"sdk_timeout      : {cfg.sdk_timeout}")
    print(f"print_raw        : {int(cfg.print_raw)}")
    print("")


def maybe_report_ret(cfg: Config, action: str, ret: int) -> None:
    """
    In send-only mode, hide expected no-robot errors such as 3102.
    In real mode, report non-zero returns.
    """
    if cfg.send_only:
        return

    if ret != 0:
        print(f"[error] {action} ret={ret}")


def main() -> int:
    cfg = Config.from_env_and_args()

    if cfg.motion_mode != "nod":
        print(f"[warn] GO2_MOTION_MODE={cfg.motion_mode!r} is not supported yet; using safe neutral nod behavior")

    print_startup(cfg)

    robot = build_backend(cfg)
    sock = make_udp_socket(cfg)

    state = BeatState()
    send_interval = 1.0 / cfg.send_hz
    next_send_time = time.monotonic()

    ret = robot.BalanceStand()
    maybe_report_ret(cfg, "BalanceStand", ret)

    print(f"[bridge] waiting for beat packets on {cfg.beat_host}:{cfg.beat_port}")
    print("[bridge] accepts JSON phase packets and beat-event packets")
    print("[bridge] Ctrl+C to stop")
    print("")

    try:
        while True:
            # Drain all pending UDP packets, but do not block.
            while True:
                try:
                    raw, addr = sock.recvfrom(65535)
                except BlockingIOError:
                    break

                data, raw_text = parse_payload(raw)
                if data is None:
                    if cfg.print_raw:
                        print(f"[RAW-UNPARSED] {raw_text}")
                    continue

                update_state_from_packet(cfg, state, data, raw_text)

            now = time.monotonic()
            if now >= next_send_time:
                pitch = calculate_pitch(cfg, state, now)
                pitch = clamp(pitch, -abs(cfg.max_pitch_rad), abs(cfg.max_pitch_rad))

                ret = robot.Euler(0.0, pitch, 0.0)
                maybe_report_ret(cfg, "Euler", ret)

                # Avoid drift if system stalls.
                next_send_time = now + send_interval

            time.sleep(0.002)

    except KeyboardInterrupt:
        print("")
        print("[bridge] stopping...")

    finally:
        try:
            ret = robot.StopMove()
            maybe_report_ret(cfg, "StopMove", ret)
        except Exception as e:
            if not cfg.send_only:
                print(f"[error] StopMove failed: {e}")

        try:
            sock.close()
        except Exception:
            pass

    print("[bridge] stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
