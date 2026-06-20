"""Autonomous visual-servoing skeleton for a DJI Mini 3 Digital Twin.

The script is deliberately dry-run by default: it runs detection, tracking,
Kalman filtering, PID control and the debug UI, but the Cyberwave command sink
only prints the velocity command.  Replace the two TODO methods in
``CyberwaveDroneAdapter`` only after Virtual Stick has been enabled on the
physical twin and the sign conventions have been calibrated in a safe area.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from queue import Empty, Full, Queue
from typing import Optional, Protocol, Union

import cv2
import numpy as np
import supervision as sv
from dotenv import load_dotenv
from filterpy.kalman import KalmanFilter
from simple_pid import PID
from ultralytics import YOLO


LOGGER = logging.getLogger("drone_servo")


@dataclass(frozen=True)
class ServoConfig:
    """All tunable quantities live here to keep flight logic predictable."""

    frame_width: int = 1920
    frame_height: int = 1080
    target_class: str = "person"
    confidence_threshold: float = 0.55
    target_width_px: float = 400.0
    vision_hz: float = 8.0
    control_hz: float = 20.0
    lost_target_timeout_s: float = 0.75
    target_reached_frames: int = 8

    # Tolerances define the target-reached state, not PID dead bands.
    x_tolerance_px: float = 45.0
    y_tolerance_px: float = 45.0
    width_tolerance_px: float = 35.0

    # Conservative command limits.  Keep these small for first live tests.
    max_yaw_deg_s: float = 12.0
    max_lateral_m_s: float = 0.20
    max_forward_m_s: float = 0.20
    max_vertical_m_s: float = 0.15

    # PID gains are intentionally gentle.  Tune one loop at a time in dry run.
    yaw_kp: float = 0.015
    yaw_ki: float = 0.0001
    yaw_kd: float = 0.002
    vertical_kp: float = 0.0008
    vertical_ki: float = 0.00001
    vertical_kd: float = 0.0002
    depth_kp: float = 0.002
    depth_ki: float = 0.00002
    depth_kd: float = 0.0005

    # Axis signs depend on the DJI coordinate convention and camera mounting.
    # Keep the default dry-run values, then calibrate each sign with one small
    # movement in an empty space before enabling a real command sink.
    yaw_sign: float = 1.0
    lateral_sign: float = 1.0
    forward_sign: float = 1.0
    vertical_sign: float = 1.0
    # With a nadir camera a smaller target is normally farther below the
    # aircraft, so it requires a descent.  DJI convention is assumed to be
    # positive = ascend; calibrate this before any real flight.
    nadir_depth_sign: float = -1.0
    spec_depth_sign: float = 1.0
    vertical_axis: str = "altitude"  # "altitude" or "forward"
    # "nadir" is physically correct for the -90° gimbal / fly-above-object
    # goal. "spec" mirrors the yaw / image-Y / bbox mapping in the brief.
    control_profile: str = "nadir"


@dataclass(frozen=True)
class TargetObservation:
    timestamp: float
    track_id: int
    confidence: float
    x_center: float
    y_center: float
    width: float
    height: float
    xyxy: tuple[int, int, int, int]


@dataclass(frozen=True)
class VisionPacket:
    timestamp: float
    frame: np.ndarray
    target: Optional[TargetObservation]


@dataclass(frozen=True)
class VelocityCommand:
    yaw_deg_s: float = 0.0
    lateral_m_s: float = 0.0
    forward_m_s: float = 0.0
    vertical_m_s: float = 0.0


@dataclass(frozen=True)
class BoundingBoxCommandConfig:
    """Calibration for :func:`compute_drone_commands`.

    DJI convention used by this function:
    - positive yaw rate rotates the aircraft to the right;
    - positive forward velocity moves it towards the target;
    - gimbal pitch is an absolute angle in degrees: 0 degrees is level and negative
      values point down.

    Confirm all three signs with a tiny manual test before using a live drone.
    """

    frame_width: int = 1920
    frame_height: int = 1080
    target_width_px: float = 400.0

    # Proportional gains: these are deliberately not PID gains.  A pure
    # function has no previous samples/integrator; use it as the safe first
    # control law, then add a stateful PID wrapper only if it is needed.
    yaw_deg_s_per_px: float = 0.015
    forward_m_s_per_px: float = 0.002
    camera_pitch_deg_per_px: float = 0.040

    max_yaw_deg_s: float = 12.0
    max_forward_m_s: float = 0.20
    min_camera_pitch_deg: float = -85.0
    max_camera_pitch_deg: float = 20.0
    center_camera_pitch_deg: float = -45.0

    x_tolerance_px: float = 45.0
    y_tolerance_px: float = 45.0
    width_tolerance_px: float = 35.0

    yaw_sign: float = 1.0
    forward_sign: float = 1.0
    camera_pitch_sign: float = 1.0


@dataclass(frozen=True)
class DroneCommands:
    """Commands generated from one target bounding box.

    ``yaw_deg_s`` is an angular velocity [deg/s].
    ``forward_m_s`` is an aircraft velocity [m/s].
    ``camera_pitch_deg`` is the requested *absolute* gimbal pitch [degrees].
    """

    yaw_deg_s: float
    forward_m_s: float
    camera_pitch_deg: float
    error_x_px: float
    error_y_px: float
    error_width_px: float
    target_reached: bool


def compute_drone_commands(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    config: BoundingBoxCommandConfig = BoundingBoxCommandConfig(),
) -> DroneCommands:
    """Return drone/gimbal commands from a single detected bounding box.

    Parameters are in image pixels: ``x1, y1`` are the top-left corner and
    ``x2, y2`` are the bottom-right corner of the bounding box. YOLO, tracking
    and video acquisition deliberately live outside this function.

    The width is the distance proxy: a target narrower than
    ``target_width_px`` produces a positive forward command.  Height is
    validated even though width is the calibrated depth signal; keeping it in
    the API avoids ambiguity about the bounding-box format and lets the caller
    pass YOLO output directly.
    """

    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        raise ValueError(
            "Expected a valid box with x2 > x1 and y2 > y1 (top-left to bottom-right)."
        )

    x = x1 + width / 2.0
    y = y1 + height / 2.0

    error_x = x - config.frame_width / 2.0
    error_y = y - config.frame_height / 2.0
    error_width = config.target_width_px - width

    # Dead bands stop constant micro-corrections when the target is close
    # enough to the desired alignment/distance.
    def outside_deadband(error: float, tolerance: float) -> float:
        if abs(error) <= tolerance:
            return 0.0
        return error - np.copysign(tolerance, error)

    yaw_input = outside_deadband(error_x, config.x_tolerance_px)
    width_input = outside_deadband(error_width, config.width_tolerance_px)
    y_input = outside_deadband(error_y, config.y_tolerance_px)

    yaw_deg_s = float(
        np.clip(
            config.yaw_sign * config.yaw_deg_s_per_px * yaw_input,
            -config.max_yaw_deg_s,
            config.max_yaw_deg_s,
        )
    )
    forward_m_s = float(
        np.clip(
            config.forward_sign * config.forward_m_s_per_px * width_input,
            -config.max_forward_m_s,
            config.max_forward_m_s,
        )
    )

    # When the target appears lower in the image (positive Y error), point
    # the gimbal further down (a more negative DJI pitch angle).
    camera_pitch_deg = float(
        np.clip(
            config.center_camera_pitch_deg
            - config.camera_pitch_sign * config.camera_pitch_deg_per_px * y_input,
            config.min_camera_pitch_deg,
            config.max_camera_pitch_deg,
        )
    )

    target_reached = (
        abs(error_x) <= config.x_tolerance_px
        and abs(error_y) <= config.y_tolerance_px
        and abs(error_width) <= config.width_tolerance_px
    )
    return DroneCommands(
        yaw_deg_s=yaw_deg_s,
        forward_m_s=forward_m_s,
        camera_pitch_deg=camera_pitch_deg,
        error_x_px=error_x,
        error_y_px=error_y,
        error_width_px=error_width,
        target_reached=target_reached,
    )


@dataclass(frozen=True)
class ControlTelemetry:
    state: "ServoState"
    command: VelocityCommand
    error_x_px: Optional[float] = None
    error_y_px: Optional[float] = None
    error_width_px: Optional[float] = None
    track_id: Optional[int] = None


class ServoState(str, Enum):
    SEARCHING = "searching"
    TRACKING = "tracking"
    REACHED = "reached"


class FrameSource(Protocol):
    def read(self) -> Optional[np.ndarray]: ...

    def close(self) -> None: ...


class CommandSink(Protocol):
    def send_velocity(self, command: VelocityCommand) -> None: ...

    def stop(self) -> None: ...


class OpenCVFrameSource:
    """Development source: webcam index or prerecorded video file."""

    def __init__(self, source: Union[int, str]) -> None:
        self._capture = cv2.VideoCapture(source)
        if not self._capture.isOpened():
            raise RuntimeError(f"Cannot open video source: {source}")

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self._capture.read()
        return frame if ok else None

    def close(self) -> None:
        self._capture.release()


class CyberwaveDroneAdapter:
    """Integration boundary for the Cyberwave DJI Mini 3 Digital Twin.

    Replace ``get_latest_frame`` and ``send_velocity`` with the commands
    exposed by the paired Cyberwave Edge for DJI app.  The skeleton keeps those
    operations isolated so the detection/control pipeline can be tested with a
    local video before any aircraft receives a command.
    """

    def __init__(self, twin_id: str) -> None:
        self.twin_id = twin_id

    def get_latest_frame(self) -> Optional[np.ndarray]:
        # TODO: use the paired drone twin, for example:
        # frame = drone.capture_frame("numpy")
        # return frame
        raise NotImplementedError(
            "Wire CyberwaveDroneAdapter.get_latest_frame() to the paired DJI Mini 3 twin."
        )

    def send_velocity(self, command: VelocityCommand) -> None:
        # TODO: translate yaw/lateral/forward/vertical into the DJI Virtual
        # Stick API through Cyberwave. Do not enable this until the twin has
        # metadata.drivers.default.virtual_stick = true and signs are tested.
        raise NotImplementedError(
            "Wire CyberwaveDroneAdapter.send_velocity() to Cyberwave Virtual Stick commands."
        )

    def stop(self) -> None:
        # TODO: send a zero velocity / hover command through Cyberwave.
        pass


class CyberwaveFrameSource:
    """FrameSource wrapper that exposes the adapter as a vision input."""

    def __init__(self, adapter: CyberwaveDroneAdapter) -> None:
        self._adapter = adapter

    def read(self) -> Optional[np.ndarray]:
        return self._adapter.get_latest_frame()

    def close(self) -> None:
        self._adapter.stop()


class DryRunCommandSink:
    """Safe default: reports commands but never transmits them to a drone."""

    def __init__(self) -> None:
        self._last_log_time = 0.0

    def send_velocity(self, command: VelocityCommand) -> None:
        now = time.monotonic()
        if now - self._last_log_time >= 0.25:
            LOGGER.info(
                "DRY RUN command yaw=%+.2f deg/s lateral=%+.3f m/s forward=%+.3f m/s vertical=%+.3f m/s",
                command.yaw_deg_s,
                command.lateral_m_s,
                command.forward_m_s,
                command.vertical_m_s,
            )
            self._last_log_time = now

    def stop(self) -> None:
        LOGGER.info("DRY RUN stop / hover")


class BoundingBoxKalman:
    """Constant-velocity Kalman filter for (center_x, center_y, width)."""

    def __init__(self) -> None:
        self._filter = KalmanFilter(dim_x=6, dim_z=3)
        self._filter.x = np.zeros((6, 1))
        self._filter.H = np.array(
            [
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            ]
        )
        self._filter.P *= 500.0
        self._filter.R = np.diag([36.0, 36.0, 64.0])
        self._filter.Q = np.eye(6) * 0.1
        self._initialized = False
        self._last_timestamp: Optional[float] = None

    def reset(self) -> None:
        self.__init__()

    def update(self, measurement: np.ndarray, timestamp: float) -> np.ndarray:
        if not self._initialized:
            self._filter.x[:3, 0] = measurement
            self._initialized = True
            self._last_timestamp = timestamp
            return measurement.copy()

        dt = max(0.001, min(timestamp - (self._last_timestamp or timestamp), 0.5))
        self._last_timestamp = timestamp
        self._filter.F = np.array(
            [
                [1.0, 0.0, 0.0, dt, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0, dt, 0.0],
                [0.0, 0.0, 1.0, 0.0, 0.0, dt],
                [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            ]
        )
        self._filter.predict()
        self._filter.update(measurement)
        return self._filter.x[:3, 0].copy()


class TargetTracker:
    """YOLO detection + ByteTrack identity selection + Kalman smoothing."""

    def __init__(self, model_path: str, config: ServoConfig) -> None:
        self._model = YOLO(model_path)
        self._config = config
        self._tracker = sv.ByteTrack()
        self._kalman = BoundingBoxKalman()
        self._selected_track_id: Optional[int] = None
        self._last_seen = 0.0

    def detect(self, frame: np.ndarray, timestamp: float) -> Optional[TargetObservation]:
        result = self._model(frame, conf=self._config.confidence_threshold, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(result)

        if len(detections) == 0 or detections.class_id is None:
            return self._handle_missing_target(timestamp)

        names = result.names
        target_mask = np.array(
            [names[int(class_id)] == self._config.target_class for class_id in detections.class_id],
            dtype=bool,
        )
        if not np.any(target_mask):
            return self._handle_missing_target(timestamp)

        tracked = self._tracker.update_with_detections(detections[target_mask])
        if len(tracked) == 0 or tracked.xyxy is None:
            return self._handle_missing_target(timestamp)

        index = self._select_detection(tracked)
        if index is None:
            return self._handle_missing_target(timestamp)

        xyxy = tracked.xyxy[index]
        x_min, y_min, x_max, y_max = (int(value) for value in xyxy)
        width = float(x_max - x_min)
        height = float(y_max - y_min)
        raw_center_x = (x_min + x_max) / 2.0
        raw_center_y = (y_min + y_max) / 2.0
        filtered = self._kalman.update(
            np.array([raw_center_x, raw_center_y, width], dtype=float), timestamp
        )
        filtered_x, filtered_y, filtered_width = filtered

        track_id = int(tracked.tracker_id[index])
        confidence = float(tracked.confidence[index])
        self._selected_track_id = track_id
        self._last_seen = timestamp

        # Retain aspect ratio only for drawing; all control uses filtered x/y/w.
        aspect_ratio = height / max(width, 1.0)
        filtered_height = filtered_width * aspect_ratio
        filtered_xyxy = (
            int(filtered_x - filtered_width / 2),
            int(filtered_y - filtered_height / 2),
            int(filtered_x + filtered_width / 2),
            int(filtered_y + filtered_height / 2),
        )
        return TargetObservation(
            timestamp=timestamp,
            track_id=track_id,
            confidence=confidence,
            x_center=float(filtered_x),
            y_center=float(filtered_y),
            width=float(filtered_width),
            height=float(filtered_height),
            xyxy=filtered_xyxy,
        )

    def _select_detection(self, tracked: sv.Detections) -> Optional[int]:
        tracker_ids = tracked.tracker_id
        if tracker_ids is None:
            return None

        if self._selected_track_id is not None:
            matching = np.where(tracker_ids == self._selected_track_id)[0]
            if len(matching):
                return int(matching[0])

        if tracked.confidence is None:
            return 0
        return int(np.argmax(tracked.confidence))

    def _handle_missing_target(self, timestamp: float) -> None:
        if timestamp - self._last_seen > self._config.lost_target_timeout_s:
            self._selected_track_id = None
            self._kalman.reset()
        return None


def put_latest(queue: Queue[VisionPacket], item: VisionPacket) -> None:
    """Keep latency low by discarding stale vision packets."""

    try:
        queue.put_nowait(item)
        return
    except Full:
        pass
    try:
        queue.get_nowait()
    except Empty:
        pass
    try:
        queue.put_nowait(item)
    except Full:
        pass


class VisionWorker(threading.Thread):
    """Independent vision pipeline: frame -> YOLO -> tracking -> filtering."""

    def __init__(
        self,
        source: FrameSource,
        tracker: TargetTracker,
        config: ServoConfig,
        output: Queue[VisionPacket],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="vision-worker", daemon=True)
        self._source = source
        self._tracker = tracker
        self._config = config
        self._output = output
        self._stop_event = stop_event
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        period = 1.0 / self._config.vision_hz
        try:
            while not self._stop_event.is_set():
                started = time.monotonic()
                frame = self._source.read()
                if frame is None:
                    time.sleep(min(period, 0.05))
                    continue

                frame = cv2.resize(frame, (self._config.frame_width, self._config.frame_height))
                packet = VisionPacket(
                    timestamp=started,
                    frame=frame,
                    target=self._tracker.detect(frame, started),
                )
                put_latest(self._output, packet)
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        except BaseException as error:  # surfaced in the main thread for clean shutdown
            self.error = error
            self._stop_event.set()


class VisualServoController:
    """PID control and target-reached state machine, independent of the SDK."""

    def __init__(self, config: ServoConfig) -> None:
        self._config = config
        # Every active profile uses exactly three PIDs: image X, image Y and
        # target width.  Only their output-to-flight-axis mixer changes.
        x_limit = config.max_lateral_m_s if config.control_profile == "nadir" else config.max_yaw_deg_s
        y_limit = config.max_forward_m_s if config.control_profile == "nadir" else config.max_vertical_m_s
        depth_limit = config.max_vertical_m_s if config.control_profile == "nadir" else config.max_forward_m_s
        self._x_pid = PID(
            config.yaw_kp,
            config.yaw_ki,
            config.yaw_kd,
            setpoint=config.frame_width / 2.0,
            sample_time=None,
            output_limits=(-x_limit, x_limit),
        )
        self._y_pid = PID(
            config.vertical_kp,
            config.vertical_ki,
            config.vertical_kd,
            setpoint=config.frame_height / 2.0,
            sample_time=None,
            output_limits=(-y_limit, y_limit),
        )
        self._depth_pid = PID(
            config.depth_kp,
            config.depth_ki,
            config.depth_kd,
            setpoint=config.target_width_px,
            sample_time=None,
            output_limits=(-depth_limit, depth_limit),
        )
        self._state = ServoState.SEARCHING
        self._reached_frames = 0

    def update(self, target: Optional[TargetObservation]) -> ControlTelemetry:
        if target is None:
            self._reset_pids()
            self._state = ServoState.SEARCHING
            self._reached_frames = 0
            return ControlTelemetry(state=self._state, command=VelocityCommand())

        # Errors are displayed in the convention requested by the specification.
        error_x = target.x_center - self._config.frame_width / 2.0
        error_y = target.y_center - self._config.frame_height / 2.0
        error_width = self._config.target_width_px - target.width

        reached = (
            abs(error_x) <= self._config.x_tolerance_px
            and abs(error_y) <= self._config.y_tolerance_px
            and abs(error_width) <= self._config.width_tolerance_px
        )
        self._reached_frames = self._reached_frames + 1 if reached else 0

        if self._reached_frames >= self._config.target_reached_frames:
            self._state = ServoState.REACHED
            return ControlTelemetry(
                state=self._state,
                command=VelocityCommand(),
                error_x_px=error_x,
                error_y_px=error_y,
                error_width_px=error_width,
                track_id=target.track_id,
            )

        self._state = ServoState.TRACKING
        x_correction = float(self._x_pid(target.x_center))
        y_correction = float(self._y_pid(target.y_center))
        depth_correction = float(self._depth_pid(target.width))

        if self._config.control_profile == "nadir":
            # At -90 degrees, X/Y in the image correspond to horizontal
            # translation on the ground plane.  Yaw only rotates the drone and
            # cannot make it fly above the object, hence it remains zero here.
            command = VelocityCommand(
                lateral_m_s=self._config.lateral_sign * x_correction,
                forward_m_s=self._config.forward_sign * y_correction,
                vertical_m_s=self._config.nadir_depth_sign * depth_correction,
            )
            return ControlTelemetry(
                state=self._state,
                command=command,
                error_x_px=error_x,
                error_y_px=error_y,
                error_width_px=error_width,
                track_id=target.track_id,
            )

        # Compatibility mixer: this is the axis mapping in the brief. It is
        # useful for a forward-facing gimbal, but is not the profile to use
        # for actually positioning above a target with a nadir camera.
        yaw = self._config.yaw_sign * x_correction
        vertical_correction = self._config.vertical_sign * y_correction
        depth_forward = self._config.spec_depth_sign * depth_correction

        if self._config.vertical_axis == "altitude":
            command = VelocityCommand(
                yaw_deg_s=yaw,
                forward_m_s=depth_forward,
                vertical_m_s=vertical_correction,
            )
        else:
            # With a non-zenith gimbal, vertical image error can represent
            # forward/backward correction.  Clamp the combined forward command.
            forward = float(
                np.clip(
                    depth_forward + vertical_correction,
                    -self._config.max_forward_m_s,
                    self._config.max_forward_m_s,
                )
            )
            command = VelocityCommand(yaw_deg_s=yaw, forward_m_s=forward)

        return ControlTelemetry(
            state=self._state,
            command=command,
            error_x_px=error_x,
            error_y_px=error_y,
            error_width_px=error_width,
            track_id=target.track_id,
        )

    def _reset_pids(self) -> None:
        self._x_pid.reset()
        self._y_pid.reset()
        self._depth_pid.reset()


def draw_debug_ui(
    packet: VisionPacket,
    telemetry: ControlTelemetry,
    config: ServoConfig,
) -> np.ndarray:
    frame = packet.frame.copy()
    center = (config.frame_width // 2, config.frame_height // 2)
    cv2.drawMarker(frame, center, (0, 255, 255), cv2.MARKER_CROSS, 36, 2)

    if packet.target is not None:
        x1, y1, x2, y2 = packet.target.xyxy
        color = (0, 255, 0) if telemetry.state == ServoState.REACHED else (255, 180, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"{config.target_class} id={packet.target.track_id} conf={packet.target.confidence:.2f}",
            (max(0, x1), max(25, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )

    lines = [
        f"STATE: {telemetry.state.value.upper()}",
        f"E_yaw: {format_error(telemetry.error_x_px)} px",
        f"E_y: {format_error(telemetry.error_y_px)} px",
        f"E_depth: {format_error(telemetry.error_width_px)} px",
        (
            "CMD [dry run] "
            f"yaw={telemetry.command.yaw_deg_s:+.2f} deg/s "
            f"lateral={telemetry.command.lateral_m_s:+.3f} m/s "
            f"forward={telemetry.command.forward_m_s:+.3f} m/s "
            f"vertical={telemetry.command.vertical_m_s:+.3f} m/s"
        ),
        "Press q or Esc to stop",
    ]
    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (20, 35 + index * 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
    return frame


def format_error(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:+.1f}"


def parse_video_source(value: str) -> Union[int, str]:
    return int(value) if value.isdigit() else value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DJI Mini 3 visual-servoing dry-run skeleton")
    parser.add_argument("--model", default="yolo11n.pt", help="Ultralytics model path or name")
    parser.add_argument("--target-class", default="person", help="YOLO class name to track")
    parser.add_argument(
        "--video-source",
        default="0",
        help="Webcam index or video path for dry-run testing. Use --cyberwave-source for the stub.",
    )
    parser.add_argument("--cyberwave-source", action="store_true", help="Exercise the Cyberwave frame-source stub")
    parser.add_argument("--drone-twin-id", default=os.getenv("CYBERWAVE_DRONE_TWIN_ID", ""))
    parser.add_argument("--target-width", type=float, default=400.0)
    parser.add_argument("--vertical-axis", choices=("altitude", "forward"), default="altitude")
    parser.add_argument(
        "--control-profile",
        choices=("nadir", "spec"),
        default="nadir",
        help="nadir: lateral/forward/altitude for a -90 degree gimbal; spec: original yaw mapping",
    )
    parser.add_argument("--confidence", type=float, default=0.55)
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    config = replace(
        ServoConfig(),
        target_class=args.target_class,
        target_width_px=args.target_width,
        vertical_axis=args.vertical_axis,
        control_profile=args.control_profile,
        confidence_threshold=args.confidence,
    )

    if args.cyberwave_source:
        if not args.drone_twin_id:
            raise SystemExit("Set CYBERWAVE_DRONE_TWIN_ID before using --cyberwave-source.")
        source: FrameSource = CyberwaveFrameSource(CyberwaveDroneAdapter(args.drone_twin_id))
    else:
        source = OpenCVFrameSource(parse_video_source(args.video_source))

    stop_event = threading.Event()
    packets: Queue[VisionPacket] = Queue(maxsize=1)
    tracker = TargetTracker(args.model, config)
    vision = VisionWorker(source, tracker, config, packets, stop_event)
    controller = VisualServoController(config)
    command_sink: CommandSink = DryRunCommandSink()

    latest_packet: Optional[VisionPacket] = None
    latest_telemetry = ControlTelemetry(state=ServoState.SEARCHING, command=VelocityCommand())
    control_period = 1.0 / config.control_hz

    LOGGER.info("Starting dry-run visual servoing. No flight command will be transmitted.")
    vision.start()
    try:
        while not stop_event.is_set():
            cycle_started = time.monotonic()
            if vision.error is not None:
                raise RuntimeError("Vision worker failed") from vision.error

            try:
                while True:
                    latest_packet = packets.get_nowait()
            except Empty:
                pass

            target = None
            if latest_packet is not None and cycle_started - latest_packet.timestamp <= config.lost_target_timeout_s:
                target = latest_packet.target

            latest_telemetry = controller.update(target)
            command_sink.send_velocity(latest_telemetry.command)

            if latest_packet is not None:
                debug = draw_debug_ui(latest_packet, latest_telemetry, config)
                cv2.imshow("DJI Mini 3 Visual Servoing - DRY RUN", debug)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    stop_event.set()

            time.sleep(max(0.0, control_period - (time.monotonic() - cycle_started)))
    finally:
        stop_event.set()
        vision.join(timeout=2.0)
        command_sink.stop()
        source.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    main()
