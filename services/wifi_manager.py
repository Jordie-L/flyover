"""WiFi manager — handles AP/client mode switching via NetworkManager."""

import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

AP_NAME = "FlyOver-Setup"
AP_IP = "192.168.4.1"
RECONNECT_TIMEOUT = 300  # 5 minutes before falling back to AP


class WiFiManager:
    def __init__(self, config):
        self.config = config
        self._mode = "unknown"  # "ap", "client", "unknown"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cached_ssids = []

    def get_mode(self):
        with self._lock:
            return self._mode

    def get_ap_ip(self):
        return AP_IP

    def get_cached_ssids(self):
        with self._lock:
            return list(self._cached_ssids)

    def _run_nmcli(self, args, timeout=30):
        try:
            result = subprocess.run(
                ["nmcli"] + args,
                capture_output=True, text=True, timeout=timeout,
            )
            return result.stdout.strip(), result.returncode
        except subprocess.TimeoutExpired:
            logger.error("nmcli timeout: %s", args)
            return "", 1
        except FileNotFoundError:
            logger.error("nmcli not found — is NetworkManager installed?")
            return "", 1

    def _scan_ssids(self):
        """Scan for available WiFi networks."""
        self._run_nmcli(["device", "wifi", "rescan"], timeout=15)
        time.sleep(3)
        output, rc = self._run_nmcli([
            "-t", "-f", "SSID,SIGNAL,SECURITY",
            "device", "wifi", "list",
        ])
        if rc != 0:
            return []

        ssids = []
        seen = set()
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                ssid = parts[0].strip()
                if ssid and ssid not in seen and ssid != AP_NAME:
                    seen.add(ssid)
                    ssids.append({
                        "ssid": ssid,
                        "signal": parts[1] if len(parts) > 1 else "",
                        "security": parts[2] if len(parts) > 2 else "",
                    })

        with self._lock:
            self._cached_ssids = ssids
        logger.info("Found %d SSIDs", len(ssids))
        return ssids

    def _start_ap(self):
        """Start the WiFi access point."""
        logger.info("Starting AP: %s", AP_NAME)

        # Delete old AP connection if exists
        self._run_nmcli(["connection", "delete", AP_NAME])

        # Create AP hotspot
        output, rc = self._run_nmcli([
            "device", "wifi", "hotspot",
            "ifname", "wlan0",
            "con-name", AP_NAME,
            "ssid", AP_NAME,
            "band", "bg",
            "channel", "6",
        ])

        if rc == 0:
            # Set static IP
            self._run_nmcli([
                "connection", "modify", AP_NAME,
                "ipv4.addresses", f"{AP_IP}/24",
                "ipv4.method", "shared",
            ])
            self._run_nmcli(["connection", "up", AP_NAME])
            with self._lock:
                self._mode = "ap"
            logger.info("AP started at %s", AP_IP)
        else:
            logger.error("Failed to start AP: %s", output)

    def _stop_ap(self):
        """Stop the access point."""
        self._run_nmcli(["connection", "down", AP_NAME])

    def _try_saved_connections(self):
        """Try to connect using saved WiFi connections."""
        output, rc = self._run_nmcli([
            "-t", "-f", "NAME,TYPE",
            "connection", "show",
        ])
        if rc != 0:
            return False

        wifi_connections = []
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1] and parts[0] != AP_NAME:
                wifi_connections.append(parts[0])

        for conn_name in wifi_connections:
            logger.info("Trying saved connection: %s", conn_name)
            _, rc = self._run_nmcli(["connection", "up", conn_name], timeout=30)
            if rc == 0:
                with self._lock:
                    self._mode = "client"
                logger.info("Connected to: %s", conn_name)
                return True

        return False

    def connect_to_network(self, ssid, password):
        """Connect to a WiFi network. Called from web server."""
        logger.info("Connecting to: %s", ssid)

        # Stop AP first
        self._stop_ap()
        time.sleep(2)

        # Try to connect
        _, rc = self._run_nmcli([
            "device", "wifi", "connect", ssid,
            "password", password,
            "ifname", "wlan0",
        ], timeout=30)

        if rc == 0:
            with self._lock:
                self._mode = "client"
            logger.info("Connected to %s", ssid)
            return True
        else:
            logger.error("Failed to connect to %s, restarting AP", ssid)
            self._start_ap()
            return False

    def rescan_networks(self):
        """Rescan WiFi — requires bringing AP down temporarily."""
        logger.info("Rescanning networks (AP will be down briefly)")
        self._stop_ap()
        time.sleep(2)
        ssids = self._scan_ssids()
        time.sleep(1)
        self._start_ap()
        return ssids

    def _is_connected(self):
        """Check if we have an active WiFi client connection."""
        output, rc = self._run_nmcli([
            "-t", "-f", "TYPE,STATE",
            "device", "status",
        ])
        for line in output.splitlines():
            if "wifi" in line and "connected" in line:
                return True
        return False

    def run(self):
        logger.info("WiFi manager starting")

        # Step 1: Try saved connections
        if self._try_saved_connections():
            # Monitor connection in client mode
            self._monitor_client()
            return

        # Step 2: No saved connection — scan and start AP
        self._scan_ssids()
        self._start_ap()

        # Monitor and wait for user to configure via portal
        while not self._stop.is_set():
            if self._mode == "client":
                self._monitor_client()
            self._stop.wait(10)

    def _monitor_client(self):
        """Monitor WiFi connection, fallback to AP on failure."""
        fail_start = None
        while not self._stop.is_set():
            if self._is_connected():
                fail_start = None
                with self._lock:
                    self._mode = "client"
            else:
                if fail_start is None:
                    fail_start = time.time()
                    logger.warning("WiFi connection lost, waiting for reconnect")
                elif time.time() - fail_start > RECONNECT_TIMEOUT:
                    logger.warning("Reconnect timeout, falling back to AP")
                    self._scan_ssids()
                    self._start_ap()
                    fail_start = None
            self._stop.wait(15)

    def stop(self):
        self._stop.set()
