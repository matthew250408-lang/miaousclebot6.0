import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


WEB_DIR = Path(__file__).resolve().parents[1]


class MaintenanceScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.TemporaryDirectory()
        os.environ["DISABLE_MAINTENANCE_CHECKER"] = "1"
        os.environ["MAINTENANCE_DATA_DIR"] = cls.data_dir.name
        os.environ["MAINTENANCE_CHECK_INTERVAL"] = "0.02"

    @classmethod
    def tearDownClass(cls):
        cls.data_dir.cleanup()
        for name in (
            "DISABLE_MAINTENANCE_CHECKER",
            "MAINTENANCE_DATA_DIR",
            "MAINTENANCE_CHECK_INTERVAL",
        ):
            os.environ.pop(name, None)

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.scheduled_at = self.now + timedelta(seconds=0.15)
        self.end_at = self.now + timedelta(seconds=0.75)
        self._write_json(
            "maintenance.json",
            {
                "bot": {
                    "active": False,
                    "message": "",
                    "start_time": None,
                    "end_time": None,
                    "show_reason": True,
                },
                "site": {
                    "active": False,
                    "message": "",
                    "start_time": None,
                    "end_time": None,
                    "show_reason": True,
                },
            },
        )
        self._write_json(
            "maintenance_history.json",
            [
                {
                    "id": "reproducible-schedule",
                    "type": "scheduled",
                    "status": "scheduled",
                    "scheduled_time": self._utc_iso(self.scheduled_at),
                    "end_time": self._utc_iso(self.end_at),
                    "message": "Maintenance planifiée",
                }
            ],
        )

    def _write_json(self, name, value):
        with open(Path(self.data_dir.name) / name, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)

    def _read_json(self, name):
        with open(Path(self.data_dir.name) / name, encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _utc_iso(value):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _get_maintenance_status(self):
        if str(WEB_DIR) not in sys.path:
            sys.path.insert(0, str(WEB_DIR))
        from app import app

        with app.test_client() as client:
            response = client.get("/api/maintenance-status")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_bot_only_maintenance_status_payload(self):
        bot_data = {
            "active": True,
            "message": "Bot maintenance",
            "start_time": "2026-09-14T10:00:00Z",
            "end_time": "2026-09-14T11:00:00Z",
            "show_reason": True,
        }
        site_data = {
            "active": False,
            "message": "",
            "start_time": None,
            "end_time": None,
            "show_reason": True,
        }
        self._write_json(
            "maintenance.json",
            {"bot": bot_data, "site": site_data},
        )

        self.assertEqual(
            self._get_maintenance_status(),
            {
                "active": True,
                "type": "bot",
                "message": "Bot maintenance",
                "bot_data": bot_data,
                "site_data": site_data,
            },
        )

    def test_bot_and_site_maintenance_status_payload(self):
        bot_data = {
            "active": True,
            "message": "Bot maintenance",
            "start_time": "2026-09-14T10:00:00Z",
            "end_time": "2026-09-14T11:00:00Z",
            "show_reason": True,
        }
        site_data = {
            "active": True,
            "message": "Site maintenance",
            "start_time": "2026-09-14T10:15:00Z",
            "end_time": "2026-09-14T10:45:00Z",
            "show_reason": False,
        }
        self._write_json(
            "maintenance.json",
            {"bot": bot_data, "site": site_data},
        )

        self.assertEqual(
            self._get_maintenance_status(),
            {
                "active": True,
                "type": "both",
                "message": "Site maintenance",
                "bot_data": bot_data,
                "site_data": site_data,
            },
        )

    def test_two_gunicorn_workers_activate_and_stop_once_in_utc(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        server_env = os.environ.copy()
        server_env.pop("DISABLE_MAINTENANCE_CHECKER", None)
        server_env["PYTHONPATH"] = str(WEB_DIR)
        server = subprocess.Popen(
            [
                "gunicorn",
                "--bind",
                f"127.0.0.1:{port}",
                "--workers",
                "2",
                "--chdir",
                str(WEB_DIR),
                "app:app",
            ],
            cwd=WEB_DIR,
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        status_url = f"http://127.0.0.1:{port}/api/maintenance-status"

        def get_status():
            with urlopen(status_url, timeout=1) as response:
                return json.loads(response.read())

        try:
            active_payload = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    payload = get_status()
                    if payload["active"]:
                        active_payload = payload
                        break
                except URLError:
                    pass
                time.sleep(0.02)

            self.assertIsNotNone(active_payload)
            self.assertEqual(active_payload["type"], "site")
            self.assertIn("bot_data", active_payload)
            self.assertIn("site_data", active_payload)
            self.assertFalse(active_payload["bot_data"]["active"])
            self.assertTrue(active_payload["site_data"]["active"])
            self.assertEqual(
                active_payload["site_data"]["message"], "Maintenance planifiée"
            )

            final_payload = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                payload = get_status()
                if not payload["active"]:
                    final_payload = payload
                    break
                time.sleep(0.02)

            self.assertIsNotNone(final_payload)
            self.assertFalse(final_payload["active"])
            self.assertEqual(final_payload["type"], "none")
            self.assertFalse(final_payload["bot_data"]["active"])
            self.assertFalse(final_payload["site_data"]["active"])

            history = self._read_json("maintenance_history.json")
            self.assertEqual(len(history), 1)
            maintenance = history[0]
            self.assertEqual(maintenance["status"], "completed")
            self.assertEqual(maintenance["type"], "active")

            started_at = datetime.fromisoformat(
                maintenance["start_time"].replace("Z", "+00:00")
            )
            ended_at = datetime.fromisoformat(
                maintenance["end_actual"].replace("Z", "+00:00")
            )
            self.assertGreaterEqual(started_at, self.scheduled_at)
            self.assertLess(started_at, self.end_at)
            self.assertGreaterEqual(ended_at, self.end_at)
        finally:
            if server.poll() is None:
                server.terminate()
            stdout, stderr = server.communicate(timeout=3)
            self.assertIn(
                server.returncode,
                (0, -15),
                msg=f"Gunicorn output:\n{stdout}\n{stderr}",
            )


if __name__ == "__main__":
    unittest.main()