# go2-audio-reactive-nod-bridge

A Python-based audio-reactive control bridge for the Unitree Go2.

The project turns live music beat/phase data into a safe stationary nod gesture:

```text
Music / audio beat detection
→ UDP phase or beat packets
→ Python nod bridge
→ BalanceStand()
→ Euler(roll=0, pitch=nod, yaw=0)
→ Unitree Go2 body-pitch nod
```

The current project supports two important workflows:

1. **Windows-only fake mode** — test the sender and packet flow without Ubuntu, SDK, or robot.
2. **Real Go2 SDK mode** — later control a real Unitree Go2 through `unitree_sdk2_python`, preferably on Ubuntu/Linux.

The motion strategy is intentionally conservative: stationary body nodding only. This project does not use walking velocity, LowCmd joint control, flips, dance actions, or unstable trick motions.

---

## Project Status

Current stable target:

```text
Windows music / beat detection
→ UDP sender
→ Python bridge
→ nod pitch calculation
→ BalanceStand()
→ Euler(0, pitch, 0)
```

The project is currently designed for:

- sender-side testing
- pretend-real/send-only testing
- later real Go2 high-level SportClient testing

---

## Repository Contents

Recommended project structure:

```text
go2_audio_reactive/
├─ README.md
├─ beat_udp_sender.py
├─ go2_audio_nod_bridge_final_v1.py
└─ requirements.txt
```

Main files:

| File | Purpose |
|---|---|
| `beat_udp_sender.py` | Detects music beat/phase and sends UDP packets |
| `go2_audio_nod_bridge_final_v1.py` | Receives UDP packets and converts them into nod commands |
| `README.md` | Project instructions |

---

## Core Motion Choice

The robot-side motion is locked to high-level SportClient commands:

```python
sport.BalanceStand()
sport.Euler(0.0, pitch_target, 0.0)
```

Command intent:

```text
1002 BalanceStand
1007 Euler(roll, pitch, yaw)
1003 StopMove on exit
```

Do not use these first:

```text
LowCmd
Move(vx, vy, vyaw)
walking velocity
Dance1 / Dance2
CrossStep
FreeBound
FreeJump
flips
HandStand
WalkUpright
```

Reason: the desired motion is a stable stationary nod gesture, not walking, drifting, or a built-in trick motion.

---

## Two Operating Modes

### 1. Windows-Only Fake Mode

Use this when:

- you do not have the robot yet
- Ubuntu cannot be installed
- you only want to test the sender
- you want to verify UDP packets and phase/beat payloads
- you want “give only, no receive from dog”

This mode does not require:

```text
Ubuntu
WSL
Unitree SDK
CycloneDDS
Real robot
Robot network
```

It only needs Python.

Flow:

```text
beat_udp_sender.py
→ UDP 127.0.0.1:5005
→ go2_audio_nod_bridge_final_v1.py
→ FakeSportClient prints commands
```

Example output:

```text
[CMD] BalanceStand()
[bridge] waiting for beat packets on 0.0.0.0:5005

[RAW] {"phase":0.240,"strength":0.68,"bpm":94.2}
[PHASE] bpm=94.2 strength=0.68 phase=0.240
[CMD] Euler(roll=+0.000, pitch=-0.0161, yaw=+0.000)
```

This proves:

```text
Audio detection works
Sender works
UDP send works
Payload format works
Bridge understands sender packets
```

### 2. Real Go2 SDK Mode

Use this later when:

- the real Unitree Go2 is available
- the robot network is ready
- `unitree_sdk2_python` is installed
- CycloneDDS is working
- the correct robot network interface is known

Recommended real setup:

```text
Windows laptop
→ music + beat_udp_sender.py
→ UDP
→ Ubuntu/Linux machine
→ go2_audio_nod_bridge_final_v1.py
→ unitree_sdk2_python SportClient
→ real Go2
```

Recommended backend:

```text
GO2_BACKEND=sdk
GO2_SEND_ONLY=0
```

For real robot control, Ubuntu/Linux is strongly recommended because the Unitree SDK2 + CycloneDDS stack is more reliable there.

---

## Requirements

### Windows Fake Mode

Recommended:

```text
Python 3.10 or 3.11
```

Create venv:

```powershell
cd C:\go2_audio_reactive_sim

py -3.10 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
```

Install dependencies needed by your sender:

```powershell
pip install numpy scipy librosa sounddevice
```

If your `beat_udp_sender.py` uses different audio packages, install those as required.

The bridge itself uses only the Python standard library in fake mode.

### Ubuntu / Linux Real Robot Mode

Recommended:

```bash
Python 3.10
unitree_sdk2_python
CycloneDDS
Unitree Go2 network access
```

Typical project path used during development:

```bash
~/unitree_sdk2_python
```

Important: use a Python 3.10 environment. Avoid Python 3.13 for SDK mode because native DDS bindings may fail.

---

## UDP Payload Format

The bridge accepts both payload styles.

### Phase Payload

Used by the normal sender:

```json
{"phase":0.25,"strength":0.7,"bpm":90}
```

| Field | Meaning |
|---|---|
| `phase` | Beat phase from `0.0` to `<1.0` |
| `strength` | Beat confidence/intensity from `0.0` to `1.0` |
| `bpm` | Estimated tempo |

### Beat Event Payload

Used by simple tests:

```json
{"beat":1,"strength":0.75,"bpm":90}
```

| Field | Meaning |
|---|---|
| `beat` | Beat trigger |
| `strength` | Beat intensity |
| `bpm` | Estimated tempo |

The final bridge supports both because earlier versions only accepted beat-event packets, while the old sender was sending phase packets.

---

## Environment Variables

### Main Bridge Variables

| Variable | Default | Purpose |
|---|---:|---|
| `GO2_BACKEND` | `fake` | `fake`, `sdk`, or `auto` |
| `GO2_SEND_ONLY` | `1` | `1` ignores SDK reply errors; `0` requires real robot success |
| `GO2_MOTION_MODE` | `nod` | Current supported mode: `nod` |
| `GO2_BEAT_PORT` | `5005` | UDP port to listen on |
| `GO2_BIND_HOST` | `0.0.0.0` | UDP bind host |
| `GO2_MAX_PITCH_RAD` | `0.025` | Maximum nod pitch in radians |
| `GO2_SEND_HZ` | `12` | Euler command send rate |
| `GO2_SDK_TIMEOUT` | `0.03` | SDK client timeout |
| `GO2_PRINT_RAW` | `0` | Print raw UDP payloads |
| `GO2_MIN_STRENGTH` | `0.0` | Ignore weak beats below this strength |
| `GO2_BEAT_DECAY_SEC` | `0.45` | Decay time for beat-event packets |
| `GO2_PHASE_SMOOTH_ALPHA` | `0.35` | Smoothing for phase packets |

### Sender Variables

| Variable | Example | Purpose |
|---|---|---|
| `GO2_BEAT_HOST` | `127.0.0.1` | UDP target IP |
| `GO2_BEAT_PORT` | `5005` | UDP target port |
| `GO2_PAYLOAD_MODE` | `phase` | Send phase-style payload |
| `GO2_MAX_ROBOT_BPM` | `120` | Cap tempo used for robot motion |
| `GO2_PHASE_BEATS` | `1` | Phase cycle length |

---

## Run: Windows Fake Mode

This is the best test mode for a laptop without Ubuntu and without robot.

### Terminal 1: Start Fake Bridge

```powershell
cd C:\go2_audio_reactive_sim
.\.venv\Scripts\activate

$env:GO2_BACKEND="fake"
$env:GO2_SEND_ONLY="1"
$env:GO2_BEAT_PORT="5005"
$env:GO2_MAX_PITCH_RAD="0.025"
$env:GO2_SEND_HZ="12"
$env:GO2_PRINT_RAW="1"

python go2_audio_nod_bridge_final_v1.py
```

### Terminal 2: Start Sender

```powershell
cd C:\go2_audio_reactive_sim
.\.venv\Scripts\activate

$env:GO2_BEAT_HOST="127.0.0.1"
$env:GO2_BEAT_PORT="5005"
$env:GO2_PAYLOAD_MODE="phase"
$env:GO2_MAX_ROBOT_BPM="120"
$env:GO2_PHASE_BEATS="1"

python beat_udp_sender.py
```

Expected bridge output:

```text
[RAW] {"phase":0.240,"strength":0.68,"bpm":94.2}
[PHASE] bpm=94.2 strength=0.68 phase=0.240
[CMD] Euler(roll=+0.000, pitch=-0.0161, yaw=+0.000)
```

---

## Run: Sender Only

You can run only the sender:

```powershell
cd C:\go2_audio_reactive_sim
.\.venv\Scripts\activate

$env:GO2_BEAT_HOST="127.0.0.1"
$env:GO2_BEAT_PORT="5005"
$env:GO2_PAYLOAD_MODE="phase"
$env:GO2_MAX_ROBOT_BPM="120"
$env:GO2_PHASE_BEATS="1"

python beat_udp_sender.py
```

However, UDP does not confirm delivery. If nothing is listening, the sender can run without proving packet content. For proper sender validation, run the fake bridge as a packet sink.

---

## Fake Beat Test

Use this PowerShell test to send artificial beat packets:

```powershell
$hostIp = "127.0.0.1"
$port = 5005
$udp = New-Object System.Net.Sockets.UdpClient

for ($i = 1; $i -le 20; $i++) {
    $msg = @{
        beat = 1
        strength = 0.75
        bpm = 90
        phase = 0.0
    } | ConvertTo-Json -Compress

    $bytes = [Text.Encoding]::UTF8.GetBytes($msg)
    [void]$udp.Send($bytes, $bytes.Length, $hostIp, $port)

    Write-Host "sent beat $i"
    Start-Sleep -Milliseconds 666
}

$udp.Close()
```

Expected bridge output:

```text
[BEAT #0001] bpm=90.0 strength=0.75
[CMD] Euler(roll=+0.000, pitch=-0.0148, yaw=+0.000)
[CMD] Euler(roll=+0.000, pitch=-0.0049, yaw=+0.000)
[CMD] Euler(roll=+0.000, pitch=+0.0000, yaw=+0.000)
```

---

## Run: Ubuntu / Linux SDK Mode Without Robot

This is useful for SDK connectivity tests before the robot is available.

```bash
cd ~/unitree_sdk2_python
conda activate unitree_go2

export GO2_BACKEND=sdk
export GO2_SEND_ONLY=1
export GO2_IFACE=eth0
export GO2_BEAT_PORT=5005
export GO2_MAX_PITCH_RAD=0.025
export GO2_SEND_HZ=12
export GO2_SDK_TIMEOUT=0.03
export GO2_PRINT_RAW=0

python3 go2_audio_nod_bridge_final_v1.py eth0
```

In send-only mode, SDK reply errors such as missing robot/service reply are suppressed because no robot is connected.

---

## Run: Real Go2 Mode Later

Use only when the real robot is connected and the network interface is correct.

```bash
cd ~/unitree_sdk2_python
conda activate unitree_go2

export GO2_BACKEND=sdk
export GO2_SEND_ONLY=0
export GO2_IFACE=eth0
export GO2_BEAT_PORT=5005
export GO2_MAX_PITCH_RAD=0.025
export GO2_SEND_HZ=12
export GO2_SDK_TIMEOUT=0.03
export GO2_PRINT_RAW=0

python3 go2_audio_nod_bridge_final_v1.py eth0
```

Recommended first real-robot pitch value:

```bash
export GO2_MAX_PITCH_RAD=0.025
```

Increase only after stable testing:

```bash
export GO2_MAX_PITCH_RAD=0.035
```

Then maybe:

```bash
export GO2_MAX_PITCH_RAD=0.045
```

Do not start with large pitch values on the real robot.

---

## Network Notes

### Windows Sender to Local Fake Bridge

Use:

```powershell
$env:GO2_BEAT_HOST="127.0.0.1"
```

### Windows Sender to Ubuntu Receiver

Find Ubuntu IP:

```bash
ip -br addr
```

Then on Windows:

```powershell
$env:GO2_BEAT_HOST="UBUNTU_IP_HERE"
```

Example:

```powershell
$env:GO2_BEAT_HOST="172.23.193.199"
```

### Windows Sender to Another Physical Linux Laptop

Use the Linux laptop’s LAN IP.

Example:

```powershell
$env:GO2_BEAT_HOST="192.168.1.50"
```

Make sure the firewall allows UDP port `5005`.

---

## Tuning Guide

### Safe Starting Values

```text
GO2_MAX_PITCH_RAD=0.025
GO2_SEND_HZ=12
GO2_PHASE_SMOOTH_ALPHA=0.35
GO2_BEAT_DECAY_SEC=0.45
```

### More Visible Nod

```text
GO2_MAX_PITCH_RAD=0.035
```

### Stronger Nod

```text
GO2_MAX_PITCH_RAD=0.045
```

Use stronger values only after basic stability is confirmed.

### Less Jitter

Increase smoothing by lowering alpha:

```text
GO2_PHASE_SMOOTH_ALPHA=0.20
```

Lower alpha means smoother/slower response.

### Faster Response

Increase smoothing alpha:

```text
GO2_PHASE_SMOOTH_ALPHA=0.50
```

Higher alpha means faster but potentially more jittery response.

---

## Troubleshooting

### Sender runs but bridge shows nothing

Check:

```text
GO2_BEAT_HOST
GO2_BEAT_PORT
GO2_BEAT_PORT on bridge
firewall
whether bridge is already running
```

For local laptop testing, use:

```powershell
$env:GO2_BEAT_HOST="127.0.0.1"
```

### Bridge says port already in use

Another process is using UDP port `5005`.

Close the other bridge or use another port:

```powershell
$env:GO2_BEAT_PORT="5006"
```

Set the same port in the sender.

### SDK mode cannot import unitree_sdk2py

Use fake mode if you are only testing sender:

```powershell
$env:GO2_BACKEND="fake"
```

For SDK mode, install and configure:

```text
unitree_sdk2_python
CycloneDDS
Python 3.10 environment
```

### SDK returns 3102

This usually means no robot/Sport service reply.

In pretend-real testing:

```text
GO2_SEND_ONLY=1
```

This suppresses expected no-robot errors.

In real robot mode:

```text
GO2_SEND_ONLY=0
```

Then `ret != 0` should be treated as a real error.

### Python 3.13 SDK issue

Avoid Python 3.13 for SDK mode.

Use Python 3.10:

```bash
conda activate unitree_go2
python --version
```

---

## Safety Notes

This project is intentionally conservative.

Real robot first test rules:

```text
Start with GO2_MAX_PITCH_RAD=0.025
Use BalanceStand first
Keep the robot on a safe flat surface
Keep space around the robot
Be ready to stop the script
Do not test strong pitch values first
Do not mix with walking commands
Do not use LowCmd until a separate safety plan exists
```

On exit, the bridge calls:

```python
StopMove()
```

---

## Development Roadmap

Planned improvements:

```text
Add visual sender monitor
Add CSV logging for bpm/phase/strength/pitch
Add optional OSC/MIDI input
Add safer startup calibration
Add real Go2 first-test checklist
Add watchdog timeout if beat packets stop
Add GUI tuning panel
Add separate simulation preview
```

Possible future motion modes:

```text
nod
sway
small yaw pulse
body bounce
```

But current stable mode remains:

```text
GO2_MOTION_MODE=nod
BalanceStand + Euler pitch only
```

---

## License

Choose a license before publishing.

Recommended for open source:

```text
MIT License
```

If this project may become commercial or robot-safety sensitive, consider a more restrictive private/commercial license.

---

## Disclaimer

This project can send movement commands to a real robot when SDK mode is enabled. Use at your own risk. Start with conservative values and test in a safe area.

The fake backend is safe and does not control a robot.

---

## Quick Start Summary

### Test sender on Windows without robot

Terminal 1:

```powershell
cd C:\go2_audio_reactive_sim
.\.venv\Scripts\activate

$env:GO2_BACKEND="fake"
$env:GO2_SEND_ONLY="1"
$env:GO2_BEAT_PORT="5005"
$env:GO2_PRINT_RAW="1"

python go2_audio_nod_bridge_final_v1.py
```

Terminal 2:

```powershell
cd C:\go2_audio_reactive_sim
.\.venv\Scripts\activate

$env:GO2_BEAT_HOST="127.0.0.1"
$env:GO2_BEAT_PORT="5005"
$env:GO2_PAYLOAD_MODE="phase"

python beat_udp_sender.py
```

### Later real robot mode

```bash
export GO2_BACKEND=sdk
export GO2_SEND_ONLY=0
export GO2_IFACE=eth0

python3 go2_audio_nod_bridge_final_v1.py eth0
```
