# 🚁 Rescue Drone

> **Autonomous search-and-rescue: a drone finds the victim, a robot dog runs to them.**

Built for the [Robotic Hackathon 2026](https://luma.com/mmc68m0b?tk=XhmbRt)  organized by Cyberwave

---

## The Goal

In a search-and-rescue scenario, time is everything. This project deploys two autonomous agents working in tandem:

1. **A DJI Mini 3 drone** takes off, scans the disaster area from the air, and locates the rescue target using computer vision.
2. Once found, it **lands near the target and signals its position** to a ground agent.
3. **A Unitree Go2 robot dog** receives the position and navigates to it — acting as the first physical responder on the ground.

The drone is the eyes in the sky; the dog is the legs on the ground.

> **Demo setup:** the rescue target is represented by a **yellow safety helmet** placed on a **blue carpet** (the simulated catastrophe area). No custom training data required — YOLO-World detects the helmet from a text prompt alone.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  DRONE (DJI Mini 3)                                             │
│                                                                 │
│  1. Takeoff                                                     │
│        │                                                        │
│  2. Search sweep ──► gimbal pitch scans + 360° yaw spins       │
│        │              + station hops until helmet visible       │
│        │                                                        │
│  3. Detect helmet ──► YOLO-World (text prompt) + HSV fallback  │
│        │                                                        │
│  4. Visual servo ──► center + approach until close enough      │
│        │              (bounding-box PD control law)             │
│        │                                                        │
│  5. Land near target & save annotated frame                     │
│        │                                                        │
│        ▼                                                        │
│  6. Signal Go2 ──────────────────────────────────────────┐     │
└──────────────────────────────────────────────────────────┼─────┘
                                                           │
┌──────────────────────────────────────────────────────────▼─────┐
│  GROUND AGENT (Unitree Go2)                                     │
│                                                                 │
│  7. Connect via Cyberwave MQTT                                  │
│  8. relative_move([1.0, 0, 0], frame="body") → advance         │
│     to the located position                                     │
└─────────────────────────────────────────────────────────────────┘
```

The search pattern sweeps the gimbal through three pitch angles (−25°, −50°, −80°) at each of six headings (60° steps), completing a full 360° circle before hopping to a new station and repeating — ensuring full area coverage without GPS.

---

## Architecture

| File | Role |
|---|---|
| `main.py` | Mission orchestrator — takeoff, search loop, visual servo, landing, Go2 handoff |
| `vision.py` | Frame decoding, HSV helmet/carpet detection, annotation overlay |
| `yolo_detect.py` | YOLO-World open-vocabulary helmet detection (text prompts, no training data) |
| `helmet_control.py` | Stateless PD control law: bounding box → yaw / forward / gimbal commands |
| `robot_navigation.py` | Unitree Go2 client — connects via Cyberwave MQTT and drives to target |
| `app.py` | Streamlit dashboard — live video, telemetry, event log, Start/Stop controls |
| `dashboard_state.py` | Thread-safe shared state between the mission thread and the Streamlit UI |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Drone platform | [DJI Mini 3](https://www.dji.com/mini-3) via Cyberwave twin `dji/dji-mini-3` |
| Ground agent | [Unitree Go2](https://www.unitree.com/go2/) via Cyberwave twin `unitree/go2` |
| Robot cloud SDK | [Cyberwave](https://cyberwave.io) (digital twin, MQTT, real-world affect) |
| Vision — primary | YOLO-World v2 (`yolov8s-worldv2.pt`) via [Ultralytics](https://ultralytics.com) |
| Vision — fallback | OpenCV HSV color thresholding |
| Dashboard | [Streamlit](https://streamlit.io) |
| Runtime / deps | Python ≥ 3.13, [uv](https://github.com/astral-sh/uv) |

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd RobHackathon

# 2. Install dependencies
uv sync

# 3. Configure credentials
cp .env.example .env
# Edit .env and fill in:
#   CYBERWAVE_API_KEY=<your key from Cyberwave Dashboard → Profile → API Tokens>
#   CYBERWAVE_ENVIRONMENT_ID=<your environment slug>
```

YOLO-World weights (`yolov8s-worldv2.pt`) are downloaded automatically on first run.

---

## Running

### Headless mission (terminal only)
```bash
make run_main
# or: uv run main.py
```

The drone takes off, searches, approaches, and lands. The Go2 advances to the target automatically after landing.

### With dashboard
```bash
make run_fe
# or: uv run streamlit run app.py
```

Opens a live Streamlit UI with drone video feed, mission status badge, FPS meter, and a scrolling event log. Use **▶ Start** to launch the mission and **⏹ Stop / Land** for an emergency landing.

### Environment toggles

| Variable | Default | Effect |
|---|---|---|
| `HELMET_USE_YOLO` | `1` | Set to `0` to use HSV color detection only (faster, less robust) |
| `HELMET_CONF` | `0.05` | YOLO-World confidence threshold (lower = more detections, more noise) |

---
