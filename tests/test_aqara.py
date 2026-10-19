import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import aqara
import web


class AqaraTests(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.settings_dir.name) / "settings.json"
        self.original_settings_file = aqara.SETTINGS_FILE
        aqara.SETTINGS_FILE = self.settings_path
        aqara._RESOURCE_INFO_CACHE.clear()

    def tearDown(self):
        aqara.SETTINGS_FILE = self.original_settings_file
        self.settings_dir.cleanup()

    def _energy_writer(self):
        module = types.ModuleType("energy")

        def write_json_file(path, data):
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            os.chmod(path, 0o600)

        module._write_json_file = write_json_file
        return patch.dict("sys.modules", {"energy": module})

    def test_signature_matches_aqara_documented_vector(self):
        signature = aqara._sign(
            app_id="4e693d54d75db580a56d1263",
            app_key="gU7Qtxi4dWnYAdmudyxni52bWZ58b8uN",
            key_id="78784564654feda454557",
            nonce="C6wuzd0Qguxzelhb",
            timestamp="1618914078668",
            access_token="532cad73c5493193d63d367016b98b27",
        )
        self.assertEqual(signature, "bfd8dd0e7c108353e6740d81e05982d8")

    def test_auth_flow_uses_current_intents_and_persists_tokens(self):
        self.settings_path.write_text(json.dumps({
            "monthly_budget": 125,
            "aqara": {
                "app_id": "app", "app_key": "secret", "key_id": "key",
                "account": "owner@example.com", "region": "US",
            },
        }), encoding="utf-8")
        with self._energy_writer(), patch.object(aqara, "_post_intent", side_effect=[
                {"authCode": "one-time-code"},
                {"accessToken": "access", "refreshToken": "refresh",
                 "expiresIn": "86400", "openId": "owner"},
        ]) as post:
            aqara.authorize_account()

        self.assertEqual(post.call_args_list[0].args[1], "config.auth.getAuthCode")
        self.assertEqual(post.call_args_list[0].args[2]["accessTokenValidity"], "30d")
        self.assertFalse(post.call_args_list[0].kwargs["include_token"])
        self.assertEqual(post.call_args_list[1].args[1], "config.auth.getToken")
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["monthly_budget"], 125)
        self.assertEqual(saved["aqara"]["access_token"], "access")
        self.assertEqual(saved["aqara"]["refresh_token"], "refresh")
        self.assertGreater(saved["aqara"]["token_expires_at"], int(time.time()))

    def test_sensor_read_uses_model_resources_and_device_name(self):
        config = {"app_id": "a", "app_key": "k", "key_id": "i",
                  "access_token": "token", "region": "US"}
        with (
            patch.object(aqara, "_ensure_access_token", return_value=config),
            patch.object(aqara, "get_devices", return_value=[{
                "did": "sensor.1", "model": "lumi.sensor_ht.test", "modelType": 3,
                "deviceName": "Kitchen", "state": 1,
            }]),
            patch.object(aqara, "_post_intent", side_effect=[
                [
                    {"resourceId": "temp", "name": "Temperature value"},
                    {"resourceId": "rh", "name": "Humidity value"},
                    {"resourceId": "battery", "name": "Battery level"},
                ],
                [
                    {"subjectId": "sensor.1", "resourceId": "temp", "value": "2155"},
                    {"subjectId": "sensor.1", "resourceId": "rh", "value": "4830"},
                    {"subjectId": "sensor.1", "resourceId": "battery", "value": "87"},
                ],
            ]) as post,
        ):
            sensors = aqara.get_sensors(config)

        self.assertEqual(sensors, [{
            "did": "sensor.1", "name": "Kitchen", "model": "lumi.sensor_ht.test",
            "temperature": 21.6, "humidity": 48.3, "battery": 87, "online": True,
        }])
        self.assertEqual(post.call_args_list[0].args[1], "query.resource.info")
        self.assertEqual(post.call_args_list[1].args[1], "query.resource.value")
        self.assertIn("resourceIds", post.call_args_list[1].args[2]["resources"][0])

    def test_device_lookup_includes_gateway_subdevices(self):
        config = {"app_id": "a", "app_key": "k", "key_id": "i",
                  "access_token": "token", "region": "US"}
        with (
            patch.object(aqara, "_ensure_access_token", return_value=config),
            patch.object(aqara, "_post_intent", side_effect=[
                {"data": [{"did": "hub.1", "modelType": 1}], "totalCount": 1},
                [{"did": "sensor.1", "model": "lumi.sensor_ht.test",
                  "modelType": 3, "parentDid": "hub.1", "state": 1}],
                {"data": [{"did": "sensor.1", "deviceName": "Hall",
                           "model": "lumi.sensor_ht.test"}]},
            ]) as post,
        ):
            devices = aqara.get_devices(config)

        sensor = next(device for device in devices if device["did"] == "sensor.1")
        self.assertEqual(sensor["deviceName"], "Hall")
        self.assertEqual(sensor["parentDid"], "hub.1")
        self.assertEqual(post.call_args_list[1].args[1], "query.device.subInfo")

    def test_api_url_uses_current_us_cloud_domain(self):
        self.assertEqual(
            aqara._api_url({"region": "US"}),
            "https://open-usa.aqara.com/v3.0/open/api",
        )

    def test_api_post_uses_v3_endpoint_and_keeps_empty_result_arrays(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":0,"result":[]}'
        config = {"app_id": "app", "app_key": "secret", "key_id": "key",
                  "access_token": "token", "region": "US"}
        with patch.object(aqara.urllib.request, "urlopen", return_value=response) as open_url:
            result = aqara._post_intent(
                config, "config.auth.getAuthCode", {"account": "owner"}, include_token=False
            )

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://open-usa.aqara.com/v3.0/open/api")
        self.assertEqual(json.loads(request.data), {
            "intent": "config.auth.getAuthCode", "data": {"account": "owner"}
        })
        self.assertFalse(any(key.lower() == "accesstoken" for key in request.headers))
        self.assertEqual(result, [])

    def test_settings_page_renders_enabled_account_authorization_form(self):
        common = {
            "last_updated": "N/A", "status_cls": "stale", "status_label": "Offline",
            "version": "test", "device_labels": {}, "panel_label": "Service Panel",
            "active_device_gid": None,
        }
        with (
            patch.object(web, "_common", return_value=common),
            patch.object(web.energy, "get_known_devices", return_value=[]),
            web.app.test_client() as client,
        ):
            response = client.get("/settings")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="aqaraAuthorizeBtn"', html)
        self.assertIn('id="aqaraAccount"', html)
        self.assertNotIn("pointer-events:none", html[html.index("panel-aqara"):html.index("panel-kasa")])
        self.assertIn("pending moderation", html)

    def test_sensor_page_shows_actionable_unconfigured_state(self):
        common = {
            "last_updated": "N/A", "status_cls": "stale", "status_label": "Offline",
            "version": "test", "device_labels": {}, "panel_label": "Service Panel",
            "active_device_gid": None,
        }
        with patch.object(web, "_common", return_value=common):
            response = web.app.test_client().get("/aqara")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Aqara not connected", response.data)
        self.assertIn(b"Go to Settings", response.data)

    def test_aqara_config_endpoint_is_local_and_validates_credentials(self):
        client = web.app.test_client()
        payload = {
            "app_id": "app", "app_key": "secret", "key_id": "key",
            "account": "owner@example.com", "region": "US",
        }
        response = client.post("/api/aqara/config", json=payload)
        self.assertEqual(response.status_code, 200)
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["aqara"]["app_key"], "secret")

        blocked = client.post(
            "/api/aqara/config", json=payload,
            environ_base={"REMOTE_ADDR": "192.0.2.1"},
        )
        self.assertEqual(blocked.status_code, 403)

        invalid = client.post(
            "/api/aqara/config", json={**payload, "region": "not-a-region"}
        )
        self.assertEqual(invalid.status_code, 400)

    def test_changing_credentials_invalidates_previous_tokens(self):
        self.settings_path.write_text(json.dumps({
            "other_setting": True,
            "aqara": {
                "app_id": "old", "app_key": "secret", "key_id": "key",
                "account": "old@example.com", "region": "US",
                "access_token": "stale", "refresh_token": "refresh",
            },
        }), encoding="utf-8")
        with self._energy_writer():
            aqara.save_app_config({
                "app_id": "new", "app_key": "secret", "key_id": "key",
                "account": "new@example.com", "region": "US",
            })
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["other_setting"])
        self.assertEqual(saved["aqara"]["access_token"], "")
        self.assertEqual(saved["aqara"]["refresh_token"], "")


if __name__ == "__main__":
    unittest.main()
