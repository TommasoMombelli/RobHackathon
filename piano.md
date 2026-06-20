# Cyberwave Hackathon — Drone Search → Dog Dispatch

## Context

Hackathon project for the Cyberwave hackathon (https://luma.com/mmc68m0b). User uploads
an image in a dashboard; a DJI Mini 3 takes off, runs open-vocabulary detection
(YOLO-World) on its video feed, and when it spots the target it hands off the GPS
position to a Unitree Go2 (or Spot) robot dog, which walks to that point.

**Build path:** simulation first using `cw.affect("simulation")`, then flip to
`cw.affect("live")` for real hardware — same code. Both real drone + real dog will be
on-site for demo day.

**Locked stack:** Cyberwave Python SDK (it wraps both REST and MQTT — no third-party
transport library needed for a remote client); Streamlit single-file dashboard;
YOLO-World via Ultralytics (Grounding DINO as fallback); everything runs on the user's
laptop (HTTPS + MQTT to Cyberwave cloud, via the SDK).

### Hardware note: DJI Mini 3 (non-Pro)
The user owns a **DJI Mini 3**, NOT the Pro. The Cyberwave catalog only lists
`dji/DJI-Mini-3-Pro` (note the mixed-case slug — quote verbatim) and `dji/dji-mini-4-pro`.
The non-Pro Mini 3 has no dedicated catalog slug. The `dji-mini-3-site-sweep` tutorial
is titled "DJI Mini 3" (non-Pro) but uses the Pro slug in code, suggesting they're
treated interchangeably at the twin level. **Plan:** use slug `dji/DJI-Mini-3-Pro` for
the twin and confirm with Cyberwave Discord / info@cyberwave.com whether a real Mini 3
can bind to that twin via the Edge for DJI Android app. Functional differences to
watch for: the non-Pro Mini 3 has a pitch-only gimbal (no roll), no obstacle sensing,
and DJI Virtual Stick support is unconfirmed for it.

### Transport note: Zenoh vs MQTT vs SDK
Cyberwave's edge runtime uses **Zenoh** as its local data bus and a **Zenoh-MQTT
bridge** forwards selected channels to the cloud MQTT broker. Zenoh is only relevant
when your code runs *on* the edge box (e.g. on the Go2's companion compute). For a
remote laptop client, **the cloud-facing transport is MQTT, but you do NOT call MQTT
directly** — the `cyberwave` Python SDK wraps both REST and MQTT into a single
interface. So no `paho-mqtt` and no `zenoh-python` in `requirements.txt`. The plan
calls SDK methods (`drone.pose.get()`, `drone.capture_frame()`, `dog.edit_position()`,
etc.); only if a needed verb (specifically the dog's `navigate_to`) turns out NOT to be
exposed by the installed SDK do we drop down to raw MQTT — listed as a **fallback**, not
the default.

**Why Cyberwave's pub/sub fits:** both twins live in one Environment and share a world
frame. The SDK already subscribes to `cyberwave/twin/{drone_uuid}/position` under the
hood for `pose.get()`; for dog dispatch we either call the SDK helper (TBD on
discovery) or publish to `cyberwave/twin/{dog_uuid}/navigate/command`. No custom
broker, no custom auth.

---

## Repo layout

```
/Users/alessio/Documents/cyberwave_hackathon/
├── app.py                # Streamlit UI (upload + status + map)
├── coordinator.py        # Orchestrator thread (search loop + handoff)
├── cyberwave_io.py       # ALL Cyberwave SDK + MQTT calls
├── detector.py           # Detector interface + Stub/YoloWorld/GroundingDino impls
├── transform.py          # GPS↔world ENU helpers (no-op in sim)
├── config.py             # Typed env-var loader
├── requirements.txt
├── .env / .env.example / .gitignore
└── scripts/
    └── smoke_cyberwave.py
```

---

## Phase 0 — Setup (≈1h)

**Goal:** auth works, both twins reachable, smoke test prints two poses.

1. **Python env**
   ```bash
   cd /Users/alessio/Documents/cyberwave_hackathon
   python3.10 -m venv .venv && source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   brew install ffmpeg          # required by cyberwave for frame decoding
   ```

2. **`requirements.txt`** (pin):
   ```
   cyberwave                 # wraps REST + MQTT; no separate transport lib needed
   streamlit>=1.36
   opencv-python>=4.10
   pillow>=10.0
   numpy>=1.26,<2.0
   matplotlib>=3.8
   python-dotenv>=1.0
   ultralytics>=8.3
   torch>=2.2
   transformers>=4.44
   # Optional fallback ONLY if Phase 0 discovery shows the SDK lacks a dog-navigate helper:
   # paho-mqtt>=2.1
   ```

3. **Cyberwave dashboard** (cyberwave.com)
   - Create Workspace `hackathon-cw` → copy `workspace_uuid`.
   - Profile → API Tokens → Create Token (shown ONCE) → save.
   - Create Environment `field-1` → copy `environment_uuid`.
   - Add twin `dji/DJI-Mini-3-Pro` → copy `drone_uuid`. (User owns the non-Pro Mini 3
     but Cyberwave's catalog only lists the Pro slug — see Context note. Verify
     binding via the Edge for DJI app with a real Mini 3 before the demo.)
   - Add twin `unitree/go2` → copy `dog_uuid`. (Same environment — they MUST share the
     world frame.)

4. **`.env`** keys: `CYBERWAVE_API_KEY`, `CYBERWAVE_WORKSPACE_ID`,
   `CYBERWAVE_ENVIRONMENT_ID`, `CYBERWAVE_BASE_URL=https://api.cyberwave.com`,
   `DRONE_TWIN_UUID`, `DOG_TWIN_UUID`, `DRONE_TWIN_SLUG=dji/DJI-Mini-3-Pro`,
   `DOG_TWIN_SLUG=unitree/go2`, `DETECTOR=stub`, `DETECTION_CONF_THRESH=0.35`,
   `SEARCH_ALTITUDE_M=2.0`, `SEARCH_YAW_STEP_DEG=30`, `SEARCH_ASCEND_STEP_M=0.5`,
   `SEARCH_MAX_ASCEND_M=6.0`.
   MQTT host/port are SDK internals — don't set them unless Phase 0 discovery shows
   you need the paho-mqtt fallback.

5. **`scripts/smoke_cyberwave.py` — also discovers the real SDK surface.**
   ```python
   from cyberwave import Cyberwave
   from config import CFG
   cw = Cyberwave(); cw.affect("simulation")
   drone = cw.twin(CFG.drone_slug); dog = cw.twin(CFG.dog_slug)
   print("drone pose:", drone.pose.get())
   print("dog   pose:", dog.pose.get())

   # Discovery: figure out what the installed SDK actually exposes, because the
   # docs and the GitHub repo disagree on twin methods like navigate_to / listen /
   # move_forward. Print the public surface so we know what to call.
   def public(obj): return sorted(a for a in dir(obj) if not a.startswith("_"))
   print("drone methods:", public(drone))
   print("dog   methods:", public(dog))
   print("cw    methods:", public(cw))
   ```
   Look for any of: `dog.navigate_to(...)`, `dog.move_to(...)`, `dog.move_forward(...)`,
   `cw.on_synchronized(...)`, `twin.listen(...)`, `twin.publish(...)`. Whatever exists
   is what `cyberwave_io.py` will call. If NOTHING dog-navigation-like exists, add
   `paho-mqtt` to `requirements.txt` and fall back to the documented topic
   `cyberwave/twin/{dog_uuid}/navigate/command` (`TwinNavigationCommandSchema` payload).

**Checkpoint:** smoke test prints two poses + a known list of twin methods to use.

---

## Phase 1 — Simulation end-to-end (≈3–5h)

**Goal:** Streamlit upload → drone takes off in Playground → spiral search → stub
detector fires → dog walks to drone's recorded position → both poses on a map.

Use `StubDetector` (fires positive after N frames) so you debug the control loop
without fighting vision models yet.

### `cyberwave_io.py` — single source of truth for Cyberwave calls

`CWBridge` class wrapping the SDK only (never imports `paho.mqtt` unless the
discovery fallback engages — see Phase 0):

- `Cyberwave()` + `cw.affect(mode)` + `cw.twin(...)` for drone + dog.
- Drone control: `takeoff(altitude=…)`, `land()`, `return_to_home()`,
  `gimbal_rotate(pitch, duration)`, `gimbal_recenter()`, `turn_left(rad)`, `ascend(m)`,
  `is_hovering()`, `get_frame("numpy")`, `pose.get()`.
- Pose polling: tight loop `bridge.drone_pose = drone.pose.get()` every 0.5s in a
  background thread. The SDK handles the underlying MQTT subscription; we just cache
  the latest dict for Streamlit and the coordinator to read.
- **Dog navigate — try SDK first, MQTT fallback**:
  ```python
  def dog_navigate_to(self, x, y, z=0.0, yaw=0.0):
      # Preferred (discovered in Phase 0 smoke test):
      if hasattr(self.dog, "navigate_to"):
          return self.dog.navigate_to(x=x, y=y, z=z, yaw=yaw, frame="world")
      if hasattr(self.dog, "move_to"):
          return self.dog.move_to(position=[x, y, z], yaw=yaw, frame="world")
      # Fallback: raw MQTT publish to the documented topic
      import json, paho.mqtt.client as mqtt  # imported lazily
      payload = {"command": "navigate", "position": [x, y, z], "yaw": yaw,
                 "frame": "world", "environment_uuid": CFG.environment_id,
                 "metadata": {"source": "drone_handoff"}}
      self._mqtt.publish(
          f"cyberwave/twin/{CFG.dog_uuid}/navigate/command",
          json.dumps(payload), qos=1,
      )
  ```
- Dog nav status: again, try `dog.on_navigate_status(cb)` / `dog.listen(...)` first; if
  absent, fall back to a paho MQTT subscribe on
  `cyberwave/twin/{dog_uuid}/navigate/status`.

### `detector.py` (Phase 1 — stub only)

`Detector` interface: `set_query(image, text_hint=None)`, `detect(frame) -> list[Detection]`.
`StubDetector` returns a fake bbox after 10 frames so the rest of the pipeline runs.

### `coordinator.py`

`Coordinator(threading.Thread)` with state machine
`IDLE → ARMING → SEARCHING → MATCH → DISPATCH → DONE/ABORT`.

Loop: takeoff → gimbal down → while not at max altitude: do a 360° yaw sweep stepping
`SEARCH_YAW_STEP_DEG`, grab frame each step, run detector; on confident hit, call
`bridge.get_pose()`, transform to world xy, `bridge.dog_navigate_to(...)`, wait for
`navigate/status` terminal state, then `rth()`.

### `app.py` (Streamlit)

- Sidebar: mode select `simulation`/`live`, detector select, `EMERGENCY LAND` button.
- `st.file_uploader` for target image + `st.text_input` for caption hint.
- **All stateful objects in `st.session_state`** — Streamlit reruns the script on every
  interaction. Never reinstantiate `CWBridge` or threads on rerun.
- Two-column live panel: latest event JSON, matplotlib scatter of drone+dog xy from
  `bridge.latest_*_pose`.
- Poll loop: `time.sleep(0.5); st.rerun()`.

**Checkpoint:** open Cyberwave Playground next to Streamlit, click "Find it", watch
drone yaw, see stub fire after ~10 frames, see dog walk to the drone's position.

**Top risk:** Streamlit reruns spawn duplicate threads + MQTT clients. **Mitigation:**
guard all init with `if "bridge" not in st.session_state`; `start_mqtt` is idempotent.

---

## Phase 2 — Real detection (≈2h)

**Goal:** Replace `StubDetector` with YOLO-World driven by the uploaded image + a text
hint typed by the user.

### Implementations in `detector.py`

- **`YoloWorldDetector` (default)** — `ultralytics.YOLO("yolov8s-world.pt")`, call
  `model.set_classes([text_hint])`, `model.predict(frame, conf=0.2, device="mps")`.
  Returns `Detection(x,y,w,h,score,label)` from `res.boxes.xywh / conf / cls`.
- **`GroundingDinoDetector` (fallback)** — HF
  `IDEA-Research/grounding-dino-tiny` with `AutoProcessor` + caption
  `f"{hint} ."` (the trailing "." matters), `post_process_grounded_object_detection`.

`build_detector(name)` factory selects by env var `DETECTOR`.

### Streamlit additions

- `st.text_input("Describe the target", placeholder="red backpack")` next to uploader
  — passed as `text_hint`.
- Third column: latest annotated frame with highest-confidence bbox drawn (cv2.rectangle).

### Performance notes
- Downscale every frame to 640px long-edge before inference.
- Apple Silicon: `device="mps"`. Otherwise CPU is ~0.3 fps — also skip every other frame.
- If still slow, run detector in its own thread with a 1-slot queue (drop frames if busy).

**Checkpoint:** upload a real photo + type its label; drone finds it in Playground;
bbox visible in Streamlit; dog dispatches.

---

## Phase 3 — Coordinate transform (≈2h)

**Goal:** in live mode, convert drone GPS → world ENU meters so the dog walks to the
right physical spot. Sim is identity (`pose.get()` already returns `x,y,z`).

### `transform.py`

`PoseAdapter(mode)` with:
- `maybe_set_origin(pose)` — on first live pose with `lat`, stash `lat0, lon0, alt0`.
- `to_world_xy(pose)` — sim returns `(pose["x"], pose["y"])`; live computes:
  ```
  R = 6_378_137.0
  east  = radians(lon - lon0) * R * cos(radians(lat0))
  north = radians(lat - lat0) * R
  ```
  Sub-100m range → <1 cm error. Plenty for a hackathon.

### Wire-up
- `CWBridge` owns a `PoseAdapter(mode)`.
- Coordinator calls `bridge.pose_adapter.maybe_set_origin(first_pose)` right after takeoff.
- **Anchor the dog twin to the takeoff point**: `dog.edit_position(0,0,0)` at startup so
  the dog's reported pose is co-located with the drone's takeoff origin.

### `normalize_pose(raw, mode)` helper
Returns a typed `Pose(x,y,z,yaw,lat?,lon?,alt?)` so the coordinator never branches on
mode-specific keys.

**Checkpoint:** inject a synthetic GPS sequence, plot commanded dog xy vs ground-truth
ENU offsets — they match.

**Watch out for:** yaw convention (compass deg vs ENU rad) — log a raw sample once and
visually confirm against Playground before trusting it for dog yaw.

---

## Phase 4 — Live cutover (≈1–2h)

**Drone:** install Cyberwave Edge for DJI Android app → log in same workspace → pair RC
via USB → scan QR code from the Environment to bind the twin → verify
`drone.get_frame("numpy")` returns an image and `cyberwave/twin/{drone_uuid}/gps` is
publishing.

**Dog:** bring up Go2 with Cyberwave companion (onboard or tethered laptop) → place
physically at drone's takeoff spot → `dog.edit_position(0,0,0)` → send
`bridge.dog_navigate_to(1.0, 0.0)` and confirm it steps forward ~1m.

**Flip mode:** Streamlit sidebar `Mode → "live"`. Same code.

### DJI Virtual Stick caveat (call out LOUDLY)
Continuous commands (`turn_left`, `ascend`, `move_forward`) on real DJI go through
Virtual Stick — region/firmware dependent, can be rejected outright.

**Mitigation, baked in before demo day:**
1. Test `drone.turn_left(0.1)` at the venue the day before.
2. If rejected, switch search pattern to discrete-only: `takeoff` → `gimbal_rotate`
   sweep → `ascend` → capture during gimbal sweeps. Detector loop unchanged.
3. Detection: if after issuing a yaw the heading didn't move in 1.5s, auto-fall-back to
   the gimbal sweep pattern in code.

**Streamlit add:** big red `EMERGENCY LAND` button calling `bridge.land()` regardless
of state. Keep a finger on the physical RC kill switch too.

**Checkpoint:** real drone takes off, finds a real backpack, real dog walks to it.

---

## Phase 5 — Polish (optional)

In priority order if time allows:

1. Live video tile in Streamlit (5 fps `get_frame` with bbox overlay) — biggest visual win.
2. Pydeck map with basemap tiles in live mode (instead of matplotlib).
3. `dog.alerts.create("target_reached", ...)` to emit a Cyberwave business event.
4. Record every MQTT pose + frame + detection to JSONL, add "Replay last run" button.

---

## Critical files

These five carry the project — all under `/Users/alessio/Documents/cyberwave_hackathon/`:

- `app.py` — Streamlit UI, session-state lifecycle, EMERGENCY LAND.
- `coordinator.py` — search/detect/dispatch state machine.
- `cyberwave_io.py` — every Cyberwave SDK + MQTT call.
- `detector.py` — `Detector` interface + Stub/YoloWorld/GroundingDino.
- `transform.py` — `PoseAdapter` + GPS↔ENU.

Supporting: `config.py`, `requirements.txt`, `.env`, `scripts/smoke_cyberwave.py`.

---

## Verification recipe

### Sim path (must pass before live)
1. `python scripts/smoke_cyberwave.py` — both poses print, SDK surface logged.
2. `streamlit run app.py` with `Mode=simulation`, `DETECTOR=stub`. Open Cyberwave
   Playground in another tab.
3. Upload any image → "Find it" → expect: drone arms, ascends to 2m, yaws in 30° steps,
   stub fires after ~10 frames, status panel cycles `arming → searching → match →
   dispatch → done`, Go2 walks to drone position, map shows both markers.
4. Switch `DETECTOR=yolo_world`, upload a photo of an object visible in the Playground
   scene, type its label, repeat. Bbox preview appears.
5. Repeat with `DETECTOR=grounding_dino` to confirm the alt path doesn't crash.
6. Mid-run, click "Stop" — coordinator aborts cleanly, drone RTHs, no zombie connections.

### Live path (demo day)
1. Pre-flight per Phase 4 — both twins "online" in Cyberwave dashboard.
2. From a REPL: `bridge.dog_navigate_to(1.0, 0.0)` then `(0.0, 0.0)` — dog walks
   forward 1m and back. World frame anchored.
3. Manual drone takeoff via RC, confirm GPS publishing in Streamlit panel. Land.
4. Place a real target (e.g. red backpack) ~10m from takeoff.
5. Streamlit `Mode=live`, `DETECTOR=yolo_world`, upload + hint "red backpack" → "Find it".
6. Expected: takeoff to 2m → yaw or gimbal sweep + ascent → bbox shown on detection →
   dispatch fires → dog walks to backpack → drone RTH on dog `arrived`.
7. Anything off → EMERGENCY LAND → physical RC takeover.

---

## Open questions to resolve empirically during Phase 0/1

(Not in the docs — pin them down with the smoke test, don't assume.)

1. **SDK dog-navigate surface**: does the installed `cyberwave` package expose
   `dog.navigate_to(...)`, `dog.move_to(...)`, or `dog.move_forward(...)`? The smoke
   test `dir(dog)` output answers this and determines whether paho-mqtt is needed.
2. **`pose.get()` shape in sim vs live**: sim likely `{x,y,z,yaw}`; live likely
   `{lat,lon,alt,heading}`. Confirm and contain in `normalize_pose`.
3. **Yaw convention** (compass vs ENU; deg vs rad). Spin once in Playground and log.
4. **DJI Mini 3 (non-Pro) binding**: confirm the non-Pro Mini 3 can bind to the
   `dji/DJI-Mini-3-Pro` twin via the Edge for DJI Android app. Ask on Discord if unsure.

---

## Reference URLs

- Overview: https://docs.cyberwave.com/overview
- Auth: https://docs.cyberwave.com/feature-reference/setup-cyberwave
- Python SDK: https://docs.cyberwave.com/overview/tools/python-sdk
- DJI Mini 3 tutorial: https://docs.cyberwave.com/tutorials/dji-mini-3-site-sweep
- Go2 tutorial: https://docs.cyberwave.com/tutorials/go2-digital-to-physical
- MQTT API: https://docs.cyberwave.com/api-reference/mqtt/main
- Zenoh-MQTT Bridge (edge-only context): https://docs.cyberwave.com/overview/tools/zenoh-mqtt-bridge
- Edge driver wire format: https://docs.cyberwave.com/feature-reference/edge/drivers/data-wire-format
- TwinNavigationCommandSchema: https://docs.cyberwave.com/api-reference/rest/TwinNavigationCommandSchema
- Hardware catalog: https://docs.cyberwave.com/hardware
- Python SDK repo: https://github.com/cyberwave-os/cyberwave-python
