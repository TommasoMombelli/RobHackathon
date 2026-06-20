"""One simple live navigation action for the Unitree Go2.

After the DJI has landed, ``main.py`` calls :func:`move_forward` once and the
Go2 drives one metre in its current body-forward direction.
"""

import time

from cyberwave import Cyberwave
from dotenv import load_dotenv


FORWARD_DISTANCE_M = 1.0
NAVIGATION_TIMEOUT_S = 15.0
MQTT_CONNECTION_TIMEOUT_S = 5.0
MQTT_POLL_INTERVAL_S = 0.1


def move_forward() -> None:
    """Move the live Go2 forward by ``FORWARD_DISTANCE_M`` once."""
    load_dotenv()

    cw = Cyberwave()
    try:
        cw.affect("live")

        go2 = cw.twin("unitree/go2")
        print(f"Go2 twin: {go2.uuid}")

        # Connect before sending the action so its completion event is not
        # missed by the MQTT subscriber.
        cw.mqtt.connect()
        deadline = time.monotonic() + MQTT_CONNECTION_TIMEOUT_S
        while not cw.mqtt.connected and time.monotonic() < deadline:
            time.sleep(MQTT_POLL_INTERVAL_S)

        if not cw.mqtt.connected:
            raise RuntimeError("Impossibile connettersi al broker MQTT Cyberwave.")

        print(f"Go2: navigo {FORWARD_DISTANCE_M:.1f} m in avanti...")
        action = go2.navigation.relative_move(
            [FORWARD_DISTANCE_M, 0.0, 0.0],
            frame="body",
        )

        action_id = action.get("action_id")
        if action_id:
            result = go2.navigation.wait_for_completion(
                action_id,
                timeout=NAVIGATION_TIMEOUT_S,
                raise_on_failure=False,
            )
            print(f"Navigazione Go2 terminata: {result.get('status')}")
        else:
            print(f"Comando Go2 inviato: {action}")
    finally:
        cw.disconnect()


if __name__ == "__main__":
    move_forward()
