"""Streamlit dashboard for the autonomous helmet-search drone.

Run with:
    uv run streamlit run app.py

main.py is NOT modified. The dashboard calls its existing public functions:
    main.takeoff(), main.look_down(), main.navigate_to_helmet(), main.land()
    main.drone          — the Cyberwave twin object (for preview + emergency land)
    main.capture()      — not called here; navigate_to_helmet() calls it internally

Live video sources:
  idle/preview  -> background thread polls main.drone.get_latest_frame()
  mission       -> background thread polls frame.png mtime
                   (main.navigate_to_helmet saves it on every loop step)

Event log: StdoutTee intercepts all print() from main.py's functions.
"""

import os
import sys
import threading
import time
from html import escape

import streamlit as st

st.set_page_config(
    page_title="Rescue Drone",
    page_icon="🚁",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    /* Streamlit otherwise inherits the browser/theme text colour, which was
       too dark against the dashboard's custom dark background. */
    .stApp { background-color: #0b1020; color: #f4f7fb; }
    .stApp p, .stApp span, .stApp label, .stApp li,
    div[data-testid="stMarkdownContainer"],
    div[data-testid="stCaptionContainer"] { color: #dce5f2; }
    .stApp h1, .stApp h2, .stApp h3,
    div[data-testid="stMetricValue"] { color: #ffffff !important; }
    div[data-testid="stMetricLabel"] { color: #b9c8dc !important; }
    .stApp button { color: #f8fbff !important; }
    div[data-testid="stMetric"] {
        background: #192235; border: 1px solid #33415c; border-radius: 8px;
        padding: 8px 14px; margin-bottom: 6px;
    }
    .badge {
        display: inline-block; padding: 4px 16px; border-radius: 20px;
        font-weight: 700; font-size: 0.9rem; letter-spacing: 0.06em;
    }
    .badge-idle    { background:#2a3448; color:#e0e8f5; }
    .badge-preview { background:#123d66; color:#c4e4ff; }
    .badge-running { background:#594600; color:#fff0a8; }
    .badge-LANDED  { background:#06450d; color:#b7ffc0; }
    .badge-ERROR   { background:#5a1010; color:#ffd0d0; }
    .log-box {
        background:#070b14; border:1px solid #3a4c6c; border-radius:6px;
        padding:10px; font-family:monospace; font-size:0.82rem;
        height:280px; overflow-y:auto; color:#edf4ff; white-space:pre-wrap;
    }
    .log-warn  { color:#ffe27a !important; }
    .log-error { color:#ff9797 !important; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ init (once)

@st.cache_resource
def _init():
    """Connect to the drone and install the stdout tee. Runs once per server process."""
    from dashboard_state import STATE, StdoutTee
    try:
        import main as _main
        error = None
    except Exception as e:
        _main = None
        error = str(e)

    # Tee sys.stdout so all print() calls from main's functions feed the log.
    if _main is not None:
        sys.stdout = StdoutTee(STATE, sys.__stdout__)

    return _main, STATE, error

main, STATE, conn_error = _init()


@st.cache_resource
def _start_background_threads(_main, _state):
    """Frame-fetching background threads. Started once, live for the process."""
    import vision

    def fetch_preview_frame():
        """Return a BGR preview frame, including the edge-camera fallback.

        ``get_latest_frame`` reads Cyberwave's cloud buffer, which can be
        empty even though the DJI camera is working.  The mission code already
        handles that case by asking ``remote_edge`` for a fresh frame; the
        dashboard needs the same path or it stays on "Waiting for first frame"
        forever.
        """
        try:
            img_bytes = _main.drone.get_latest_frame()
            if img_bytes:
                try:
                    return vision.decode_frame(img_bytes)
                except (vision.NoFrameError, ValueError):
                    # A JSON "no frame" response is normal for this endpoint.
                    pass
        except Exception:
            pass

        try:
            frame = _main.drone.get_frame(format="numpy", source="remote_edge")
            if frame is None or getattr(frame, "size", 0) == 0:
                return None
            # ``remote_edge`` normally returns a BGR NumPy image.  Accept a
            # byte payload too, in case the driver implementation changes.
            if getattr(frame, "ndim", None) == 3:
                return frame
            return vision.decode_frame(frame)
        except Exception:
            return None

    def frame_loop():
        frame_png = os.path.join(os.path.dirname(__file__), "frame.png")
        last_mtime = 0.0

        while True:
            if _state.running:
                # Mission is active: main.navigate_to_helmet() writes frame.png each step.
                try:
                    mtime = os.path.getmtime(frame_png)
                    if mtime != last_mtime:
                        last_mtime = mtime
                        _state.publish_frame_png(frame_png)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
                time.sleep(0.4)
            else:
                # Idle / preview: use the cloud buffer, then a fresh edge
                # capture when that buffer has no frame.
                try:
                    frame = fetch_preview_frame()
                    if frame is not None:
                        helmet = vision.detect_helmet(frame)
                        carpet = vision.detect_carpet(frame)
                        annotated = vision.annotate(frame, helmet=helmet, carpet=carpet)
                        _state.publish_frame_bgr(annotated)
                        _state.update(mode="preview")
                except Exception:
                    time.sleep(1.0)
                time.sleep(0.7)   # ~1.4 fps; Cyberwave buffer is intermittent

    t = threading.Thread(target=frame_loop, daemon=True, name="frame-loop")
    t.start()
    return t

if main is not None:
    _start_background_threads(main, STATE)


# ------------------------------------------------------------------ mission thread

def _run_mission():
    STATE.update(running=True, mode="running")
    STATE.log("Mission started.")
    try:
        if not main.takeoff():
            STATE.log("Mission cancelled before takeoff completed.", "warn")
            return
        main.look_down()
        reached = main.navigate_to_helmet()
        if reached:
            STATE.log("✅ Mission complete: helmet located.")
        else:
            STATE.log("Mission ended without reaching the helmet.", "warn")
    except Exception as e:
        STATE.log(f"Mission error: {e}", "error")
        STATE.update(mode="ERROR")
    finally:
        try:
            main.land()
        except Exception:
            pass
        STATE.update(running=False, mode="LANDED")
        STATE.log("Drone landed.")


# ------------------------------------------------------------------ UI

st.markdown("# 🚁 Rescue Drone Dashboard")
st.caption("Autonomous helmet-search · Cyberwave / DJI Mini 3")

if conn_error:
    st.error(f"Drone connection failed: {conn_error}\n\nCheck your `.env` file.")
    st.stop()

# --- control buttons (outside fragment so clicks are never swallowed) ---
st.divider()
b1, b2, _ = st.columns([1, 1, 5])

start_clicked = b1.button("▶ Start", type="primary", disabled=STATE.running,
                           use_container_width=True)
stop_clicked  = b2.button("⏹ Stop / Land", disabled=not STATE.running,
                           use_container_width=True)

if start_clicked and not STATE.running:
    main.reset_mission()
    t = threading.Thread(target=_run_mission, daemon=True, name="mission")
    STATE.nav_thread = t
    t.start()
    st.rerun()

if stop_clicked:
    STATE.log("Emergency stop requested from dashboard.", "warn")
    try:
        main.stop_and_land()
    except Exception as e:
        STATE.log(f"Drone landing failed: {e}", "error")
    STATE.update(running=False, mode="LANDED")
    st.rerun()

st.divider()

# --- live panel (auto-refreshes every 350 ms) ---
@st.fragment(run_every=0.35)
def _live():
    # These columns must belong to the fragment.  Elements written into a
    # container created *outside* a recurring fragment accumulate on each
    # refresh, which was producing an endless stack of log panes.
    vid_col, telem_col = st.columns([3, 2])
    snap = STATE.snapshot()
    jpeg, _ = STATE.get_frame()

    with vid_col:
        if jpeg:
            st.image(jpeg, use_container_width=True)
        else:
            st.info("⏳ Waiting for first frame from the drone camera…")
        conn = "🔴 No signal" if snap["stale"] else "🟢 Live"
        st.caption(f"{conn}  ·  {snap['fps']} fps")

    with telem_col:
        mode = snap["mode"]
        css = f"badge-{mode}" if mode in ("idle","preview","running","LANDED","ERROR") else "badge-idle"
        st.markdown(f'<span class="badge {css}">{mode.upper()}</span>',
                    unsafe_allow_html=True)
        st.markdown("")
        st.metric("FPS", snap["fps"])

        lines = STATE.recent_log_lines(50)
        log_html = ""
        for line in reversed(lines):
            if "⚠" in line:
                log_html += f'<div class="log-warn">{escape(line)}</div>'
            elif "✖" in line:
                log_html += f'<div class="log-error">{escape(line)}</div>'
            else:
                log_html += f"<div>{escape(line)}</div>"
        st.markdown("**Event log**")
        st.markdown(f'<div class="log-box">{log_html}</div>', unsafe_allow_html=True)


_live()

# --- snapshot ---
st.divider()
s1, _ = st.columns([1, 4])
with s1:
    if st.button("📸 Save Snapshot", use_container_width=True):
        path = f"snapshot_{int(time.time())}.png"
        if STATE.save_snapshot(path):
            st.success(f"Saved `{path}`")
            data = STATE.get_latest_snapshot_bytes()
            if data:
                st.download_button("⬇ Download", data=data,
                                   file_name=path, mime="image/jpeg",
                                   use_container_width=True)
        else:
            st.warning("No frame available yet.")

st.caption("🤖 RobHackathon 2026 · powered by Cyberwave + Claude")
