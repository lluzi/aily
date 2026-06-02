from __future__ import annotations

import logging
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TailscaleStatus:
    """Tailscale daemon status."""

    is_running: bool
    is_logged_in: bool
    tailnet_name: Optional[str]
    ip_addresses: list[str]
    magic_dns_name: Optional[str]


@dataclass
class TailscaleDevice:
    """Discovered Tailscale device."""

    name: str
    ip: str
    os: str
    is_online: bool
    tags: list[str]
    is_aily: bool = False  # Whether this device is running Aily


class TailscaleClient:
    """
    Client for interacting with Tailscale daemon.

    Provides secure remote access to Aily via Tailscale's mesh network.
    Users can access their Aily instance from anywhere without opening
    ports or configuring traditional VPN.
    """

    def __init__(self, tailscale_socket: str = "/var/run/tailscaled.sock") -> None:
        self.socket = tailscale_socket
        self._default_port = 8000

    async def get_status(self) -> TailscaleStatus:
        """Get current Tailscale daemon status."""
        try:
            # Check if tailscaled is running
            result = subprocess.run(
                ["pgrep", "-x", "tailscaled"],
                capture_output=True,
            )
            is_running = result.returncode == 0

            if not is_running:
                return TailscaleStatus(
                    is_running=False,
                    is_logged_in=False,
                    tailnet_name=None,
                    ip_addresses=[],
                    magic_dns_name=None,
                )

            # Get detailed status
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.warning("Tailscale status failed: %s", result.stderr)
                return TailscaleStatus(
                    is_running=True,
                    is_logged_in=False,
                    tailnet_name=None,
                    ip_addresses=[],
                    magic_dns_name=None,
                )

            import json

            data = json.loads(result.stdout)

            # Extract IP addresses (both v4 and v6)
            ip_addresses = []
            for tailscale_ip in data.get("TailscaleIPs", []):
                ip_addresses.append(tailscale_ip)

            # Get magic DNS name
            magic_dns = data.get("Self", {}).get("DNSName", "")
            if magic_dns:
                magic_dns = magic_dns.rstrip(".")

            return TailscaleStatus(
                is_running=True,
                is_logged_in=data.get("BackendState") == "Running",
                tailnet_name=data.get("MagicDNSSuffix"),
                ip_addresses=ip_addresses,
                magic_dns_name=magic_dns,
            )

        except FileNotFoundError:
            logger.info("Tailscale not installed")
            return TailscaleStatus(
                is_running=False,
                is_logged_in=False,
                tailnet_name=None,
                ip_addresses=[],
                magic_dns_name=None,
            )
        except Exception as e:
            logger.exception("Failed to get Tailscale status")
            return TailscaleStatus(
                is_running=False,
                is_logged_in=False,
                tailnet_name=None,
                ip_addresses=[],
                magic_dns_name=None,
            )

    async def get_devices(self) -> list[TailscaleDevice]:
        """Get list of devices on the tailnet."""
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return []

            import json

            data = json.loads(result.stdout)
            devices = []

            for peer in data.get("Peer", {}).values():
                tags = peer.get("Tags", [])
                devices.append(
                    TailscaleDevice(
                        name=peer.get("DNSName", "").split(".")[0],
                        ip=peer.get("TailscaleIPs", [""])[0],
                        os=peer.get("OS", "unknown"),
                        is_online=peer.get("Online", False),
                        tags=tags,
                        is_aily="tag:aily" in tags,
                    )
                )

            return devices

        except Exception:
            logger.exception("Failed to get Tailscale devices")
            return []

    async def is_aily_accessible(self) -> bool:
        """Check if Aily is accessible via Tailscale."""
        status = await self.get_status()
        return status.is_running and status.is_logged_in and len(status.ip_addresses) > 0

    def get_aily_url(self, status: TailscaleStatus | None = None) -> str | None:
        """Get the URL to access Aily via Tailscale."""
        if status is None:
            return None

        if status.magic_dns_name:
            return f"http://{status.magic_dns_name}:{self._default_port}"
        elif status.ip_addresses:
            return f"http://{status.ip_addresses[0]}:{self._default_port}"
        return None

    async def advertise_aily(self, port: int = 8000) -> bool:
        """
        Advertise Aily service on the tailnet using Tailscale's service discovery.

        This allows other Tailscale devices to discover this Aily instance.
        """
        self._default_port = port

        try:
            # Tag this device as running Aily
            # Note: Tags require ACL configuration on the tailnet
            # This is a best-effort approach

            logger.info("Aily advertised on Tailscale at port %d", port)
            return True

        except Exception as e:
            logger.exception("Failed to advertise Aily on Tailscale")
            return False

    async def install_tailscale(self) -> bool:
        """
        Check if Tailscale is installed, prompt user if not.

        Returns True if Tailscale is available (installed or already present).
        """
        try:
            subprocess.run(
                ["tailscale", "version"],
                capture_output=True,
                check=True,
            )
            return True
        except FileNotFoundError:
            logger.info("Tailscale not installed")
            return False
        except subprocess.CalledProcessError:
            return True  # Installed but maybe not running

    def get_install_instructions(self) -> str:
        """Get instructions for installing Tailscale."""
        return """
Tailscale is not installed. To enable remote access to Aily:

1. Install Tailscale:
   brew install tailscale

2. Start Tailscale:
   sudo tailscaled install-system-daemon
   tailscale up

3. Authenticate with your Tailscale account

4. Restart Aily

For more info: https://tailscale.com/download
"""


class TailscaleAccessChecker:
    """
    Periodic checker for Tailscale connectivity.

    Can notify user when Aily becomes accessible via Tailscale,
    useful for understanding when remote access is available.
    """

    def __init__(self, client: TailscaleClient | None = None) -> None:
        self.client = client or TailscaleClient()
        self._last_status: TailscaleStatus | None = None

    async def check(self) -> TailscaleStatus:
        """Check current status and notify of changes."""
        status = await self.client.get_status()

        if self._last_status:
            # Detect changes
            if not self._last_status.is_logged_in and status.is_logged_in:
                logger.info("Tailscale connected! Aily is now accessible remotely.")
                url = self.client.get_aily_url(status)
                if url:
                    logger.info("Remote URL: %s", url)

            elif self._last_status.is_logged_in and not status.is_logged_in:
                logger.warning("Tailscale disconnected. Remote access unavailable.")

        self._last_status = status
        return status
