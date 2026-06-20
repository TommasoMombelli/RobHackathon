from cyberwave import Cyberwave
from time import sleep
import os
import math
import warnings
from dotenv import load_dotenv

# get_latest_frame() works reliably here; silence its deprecation notice.
warnings.filterwarnings("ignore", message=r".*get_latest_frame.*deprecated.*")
from PIL import Image
from IPython.display import display

import vision
import helmet_control as hc

load_dotenv()

CYBERWAVE_API_KEY = os.environ["CYBERWAVE_API_KEY"]
CYBERWAVE_ENV_SLUG = os.environ["CYBERWAVE_ENVIRONMENT_ID"]


cw = Cyberwave(api_key=CYBERWAVE_API_KEY, environment_id=CYBERWAVE_ENV_SLUG)
cw.affect("real-world")  # commands target the real DJI aircraft (source_type="tele")
drone = cw.twin("dji/dji-mini-3")


def takeoff():
    print("Taking off…")
    # NOTE: on a real DJI Mini the altitude arg is ignored (firmware default
    # ~1.2 m); it only matters in the simulator. There is no SDK ascend command,
    # so we gain coverage with a shallow gimbal + translation, not altitude.
    drone.takeoff(altitude=2.5)
    sleep(5)

def search_target():
    drone.move_forward(distance=1.0)
    drone.move_backward(distance=1.0)
    print("Waiting for moving camera…")
    drone.gimbal_rotate(pitch=-90.0, duration=5.5)


def take_image():
    frame = capture()  # robust: retries + remote_edge fallback
    print("Saving image...")
    rgb = Image.fromarray(frame[:, :, ::-1])  # BGR -> RGB
    rgb.save("frame.png", "PNG")
    display(rgb)


def capture(retries=8, delay=1.5):
    """Grab the latest frame as a BGR OpenCV image.

    The Cyberwave cloud frame buffer is intermittent — it returns a JSON
    "no frame available" body when the camera isn't streaming. We retry, and as
    a last resort force a fresh photo through the edge driver (remote_edge).
    """
    for attempt in range(retries):
        img_bytes = drone.get_latest_frame()
        try:
            if img_bytes:
                return vision.decode_frame(img_bytes)
            print(f"  no frame yet (attempt {attempt + 1}/{retries})…")
        except vision.NoFrameError as e:
            print(f"  {e} (attempt {attempt + 1}/{retries})")
        except ValueError as e:
            print(f"  frame decode failed: {e}")
        sleep(delay)

    # Cloud buffer never produced a frame — ask the drone to take one directly.
    try:
        print("  forcing a fresh photo via edge driver (remote_edge)…")
        frame = drone.get_frame(format="numpy", source="remote_edge")
        if frame is not None and getattr(frame, "size", 0) > 0:
            return frame
    except Exception as e:
        print(f"  remote_edge capture failed: {e}")

    raise RuntimeError("Camera returned no usable frame (is the drone camera streaming?)")


# --- gimbal pitch schedule -----------------------------------------------
SEARCH_PITCH = -45.0    # oblique: wide forward view to spot helmets across the room
CLOSE_PITCH = -90.0     # straight down: precise when hovering over the target

_gimbal_pitch = None     # track last commanded pitch to avoid redundant moves


def set_gimbal_pitch(pitch, *, force=False):
    """Move the gimbal to `pitch` degrees, skipping tiny/no-op changes."""
    global _gimbal_pitch
    # Wide hysteresis: only move the gimbal for a meaningful change. Frequent
    # small tilts shift the image and destabilize detection / the control loop.
    if not force and _gimbal_pitch is not None and abs(_gimbal_pitch - pitch) < 12:
        return
    print(f"Gimbal -> {pitch:.0f}°")
    drone.gimbal_rotate(pitch=pitch, duration=2.5)
    _gimbal_pitch = pitch
    sleep(1)


def look_down():
    """Set the initial oblique search view (wide forward coverage)."""
    print("Setting oblique search view…")
    set_gimbal_pitch(SEARCH_PITCH, force=True)


# --- navigation tuning ---------------------------------------------------
# Helmet bbox width (fraction of frame width) at which we're "close enough" to
# land. THE calibration knob: report the `w=...px` printed at closest approach
# and set this to (that width / frame_width). Larger -> drone gets closer.
TARGET_WIDTH_FRAC = 0.30
TRACK_PITCH = -50.0     # fixed gimbal angle while approaching (stable width signal)
STEP_SECONDS = 1.0      # turns the controller's velocities into per-step moves
MAX_STEPS = 80          # safety cap on the search/approach loop
# Don't drive forward while badly off-heading (it steers us off the target).
FORWARD_X_GATE_FRAC = 0.22
# Plateau safeguard: if centered on the helmet but the width stops growing for
# this many steps (can't get any closer), land anyway.
PLATEAU_STEPS = 4
PLATEAU_DELTA_PX = 6           # width growth below this counts as "not closer"
MIN_LAND_WIDTH_FRAC = 0.12     # don't land if the helmet is still tiny/far

# --- active search pattern -----------------------------------------------
# Gimbal pitches swept at each heading: shallow (far across the room) -> steep
# (the floor right below). Shallow angles are how we "see more" without altitude.
SEARCH_PITCHES = (-25.0, -50.0, -80.0)
SEARCH_HEADINGS = 6                 # 6 x 60° = a full 360° turn per station
SEARCH_YAW_STEP = math.radians(60)  # radians per heading change
STATION_HOP = 1.5                   # meters to a new search spot after a full spin


def search_actions():
    """Yield search moves: sweep the gimbal at each heading, spin a full circle,
    then hop to a new station and repeat. Caller captures+detects between moves.

    Each yielded item is (kind, value):
      ("pitch", deg)  -> tilt gimbal to look far/mid/near
      ("yaw", rad)    -> rotate the drone to the next heading
      ("hop", meters) -> translate to a fresh area to cover new ground
    """
    while True:
        for _ in range(SEARCH_HEADINGS):
            for pitch in SEARCH_PITCHES:
                yield ("pitch", pitch)
            yield ("yaw", SEARCH_YAW_STEP)
        yield ("hop", STATION_HOP)


def _bbox_config(frame):
    """Build the control-law config for the current frame size.

    Gains are scaled up from go_to_helmet's velocity defaults because our frames
    are small (~640px) and we issue one discrete move per loop rather than a
    continuous 20 Hz velocity stream.
    """
    h, w = frame.shape[:2]
    return hc.BoundingBoxCommandConfig(
        frame_width=w,
        frame_height=h,
        target_width_px=TARGET_WIDTH_FRAC * w,
        yaw_deg_s_per_px=0.08,
        forward_m_s_per_px=0.004,
        camera_pitch_deg_per_px=0.25,
        max_yaw_deg_s=20.0,
        max_forward_m_s=0.8,
        # We land NEAR the helmet, not perfectly overhead, so don't let vertical
        # position block arrival: arrival = centered horizontally + close (width).
        y_tolerance_px=float(h),
        center_camera_pitch_deg=SEARCH_PITCH,   # -45° when target is centered vertically
        min_camera_pitch_deg=CLOSE_PITCH,       # -90° straight down
        max_camera_pitch_deg=0.0,
    )


def navigate_to_helmet():
    """Visually servo the drone from its corner to the yellow helmet.

    Each loop: grab a top-down frame, find the yellow helmet, and nudge the
    drone so the helmet moves toward the image center. When the helmet is
    centered and large enough, we are above it and stop.
    Returns True if we reached the helmet, False if we gave up.
    """
    searcher = search_actions()
    mode = "search"            # "search" = scanning, "track" = approaching helmet
    lost = 0                   # consecutive frames without the helmet while tracking
    best_width = 0.0           # widest helmet seen so far this approach
    stale = 0                  # consecutive steps without getting closer
    for step in range(MAX_STEPS):
        frame = capture()
        helmet = vision.detect_helmet(frame)
        carpet = vision.detect_carpet(frame)

        # Save an annotated frame so we can watch progress / retune colors.
        cv_dbg = vision.annotate(frame, helmet=helmet, carpet=carpet)
        Image.fromarray(cv_dbg[:, :, ::-1]).save("frame.png", "PNG")

        if helmet is None:
            # If we were approaching, tolerate a couple of dropped detections
            # before giving up the lock and going back to scanning.
            if mode == "track":
                lost += 1
                if lost < 3:
                    print(f"[{step}] briefly lost the helmet ({lost}/3) — holding…")
                    sleep(1)   # DJI auto-hovers when no command is sent
                    continue
                print(f"[{step}] lost the helmet — back to SEARCH mode.")
                mode = "search"
                searcher = search_actions()   # restart the sweep from this spot
                best_width, stale = 0.0, 0
            # Actively explore: sweep the gimbal, rotate, and hop to new spots
            # so we cover the whole area instead of spinning in one place.
            kind, val = next(searcher)
            if kind == "pitch":
                print(f"[{step}] scanning — gimbal {val:.0f}°")
                set_gimbal_pitch(val, force=True)
            elif kind == "yaw":
                print(f"[{step}] scanning — rotate {math.degrees(val):.0f}°")
                drone.turn_left(angle=val)
                sleep(1.5)
            elif kind == "hop":
                print(f"[{step}] scanning — hop to new station ({val} m)")
                drone.move_forward(distance=val)
                sleep(1.5)
            continue

        # Helmet is visible.
        lost = 0
        if mode == "search":
            print(f"[{step}] 👁️  I can see the helmet — switching to approach.")
            mode = "track"
            best_width, stale = 0.0, 0

        # Hold the gimbal at a fixed moderate angle while approaching. A stable
        # view means the helmet's width grows monotonically as we close in — a
        # clean distance signal with no pitch/err_y oscillation.
        set_gimbal_pitch(TRACK_PITCH)

        # Feed the YOLO bounding box to the extracted control law.
        x, y, w, h = helmet[3]
        cfg = _bbox_config(frame)
        cmd = hc.compute_drone_commands(x, y, x + w, y + h, cfg)
        width_px = cfg.target_width_px - cmd.error_width_px   # actual bbox width

        # Track how close we're getting (widest helmet so far).
        if width_px > best_width + PLATEAU_DELTA_PX:
            best_width = width_px
            stale = 0
        else:
            stale += 1

        centered_x = abs(cmd.error_x_px) < cfg.x_tolerance_px
        plateaued = (
            centered_x
            and stale >= PLATEAU_STEPS
            and width_px >= MIN_LAND_WIDTH_FRAC * cfg.frame_width
        )
        print(f"[{step}] err_x={cmd.error_x_px:+.0f} err_y={cmd.error_y_px:+.0f} "
              f"w={width_px:.0f}px (best {best_width:.0f}, stale {stale}) | "
              f"yaw={cmd.yaw_deg_s:+.1f}°/s fwd={cmd.forward_m_s:+.2f}m/s "
              f"carpet={'yes' if carpet is not None else 'no'}")

        if cmd.target_reached or plateaued:
            why = "at target width" if cmd.target_reached else "as close as it gets"
            print(f"✅ Reached the helmet ({why}) — landing nearby.")
            sleep(1)           # settle (DJI auto-hovers with no command sent)
            take_image()       # record the helmet before descending
            drone.land()
            return True

        # Map the controller's velocities onto discrete SDK moves.
        # Yaw: deg/s -> per-step angle in radians.
        if cmd.yaw_deg_s != 0.0:
            angle = math.radians(abs(cmd.yaw_deg_s)) * STEP_SECONDS
            if cmd.yaw_deg_s > 0:
                drone.turn_right(angle=angle)   # target right of center
            else:
                drone.turn_left(angle=angle)
            sleep(1)

        # Forward: only when roughly facing the target. Driving forward while
        # badly off-heading steers us off the helmet (caused the step-5 swing).
        if cmd.forward_m_s != 0.0 and abs(cmd.error_x_px) < FORWARD_X_GATE_FRAC * cfg.frame_width:
            dist = abs(cmd.forward_m_s) * STEP_SECONDS
            if cmd.forward_m_s > 0:
                drone.move_forward(distance=dist)   # target appears far (small)
            else:
                drone.move_backward(distance=dist)
            sleep(1.5)

    print("Gave up — helmet not reached within step budget.")
    return False


def verify_movement():
    """Check whether move/turn commands actually move the real aircraft.

    Grabs a frame, sends a move + turn, grabs another, and reports how much
    the image changed. If the drone is truly stationary (commands dropped by
    the DJI driver), the two frames are nearly identical.
    """
    import numpy as np
    before = capture()
    print("Sending move_forward(0.6) + turn_left(0.4)…")
    drone.move_forward(distance=0.6)
    sleep(2)
    drone.turn_left(angle=0.4)
    sleep(2)
    after = capture()

    h = min(before.shape[0], after.shape[0])
    w = min(before.shape[1], after.shape[1])
    diff = np.abs(before[:h, :w].astype(int) - after[:h, :w].astype(int)).mean()
    print(f"Mean pixel change between frames: {diff:.1f} (0-255)")
    if diff < 4:
        print("⚠️  Frames barely changed — the drone likely did NOT move "
              "(movement commands dropped by the DJI driver).")
    else:
        print("✅ Frames changed — the drone is responding to movement commands.")
    return diff


def land():
    drone.land()
    drone.land()


if __name__ == "__main__":
    try:
        takeoff()
        look_down()
        # Uncomment to check whether the drone actually responds to movement:
        # verify_movement()
        reached = navigate_to_helmet()   # lands near the helmet on success
        if reached:
            print("Mission complete: helmet located in the catastrophe area.")
        else:
            print("Mission ended without reaching the helmet.")
    finally:
        land()   # safety: harmless if already landed


