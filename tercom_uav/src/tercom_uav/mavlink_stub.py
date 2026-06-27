"""MAVLink integration placeholder.

The prototype intentionally has no mandatory ROS/PX4 dependency. Replace this
stub with a pymavlink or MAVSDK publisher when integrating with an autopilot.
"""

from __future__ import annotations

from tercom_uav.types import NavigationEstimate


class MavlinkBridgeStub:
    """Small adapter boundary for future autopilot integration."""

    def send_estimate(self, estimate: NavigationEstimate) -> dict[str, float | bool]:
        """Return the payload that a real MAVLink bridge would publish."""

        return estimate.to_dict()

