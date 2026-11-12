import unittest
from types import SimpleNamespace
from unittest.mock import patch


def launch_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.app import main, parse_args
        from tools.robot_cpg_gui.devices import resolve_serial_port
    except ImportError as exc:
        testcase.fail(f"launcher API is not implemented: {exc}")
    return main, parse_args, resolve_serial_port


class LaunchTests(unittest.TestCase):
    def test_parse_args_separates_real_mock_and_smoke_modes(self):
        _, parse_args, _ = launch_api(self)

        real = parse_args([])
        mock = parse_args(["--mock"])
        smoke = parse_args(["--mock", "--smoke-test"])

        self.assertFalse(real.mock)
        self.assertFalse(real.smoke_test)
        self.assertTrue(mock.mock)
        self.assertFalse(mock.smoke_test)
        self.assertTrue(smoke.mock)
        self.assertTrue(smoke.smoke_test)

    def test_explicit_serial_port_wins_over_discovery(self):
        _, _, resolve_serial_port = launch_api(self)

        selected = resolve_serial_port("/dev/robot-servo", candidates=["/dev/ttyUSB0"])

        self.assertEqual(selected, "/dev/robot-servo")

    def test_auto_serial_uses_first_candidate_or_reports_actionable_error(self):
        _, _, resolve_serial_port = launch_api(self)

        self.assertEqual(
            resolve_serial_port("AUTO", candidates=["/dev/ttyUSB2", "/dev/ttyUSB3"]),
            "/dev/ttyUSB2",
        )
        with self.assertRaisesRegex(RuntimeError, "--port"):
            resolve_serial_port("AUTO", candidates=[])

    def test_mock_smoke_entry_constructs_and_closes_without_hardware(self):
        main, _, _ = launch_api(self)

        result = main(["--mock", "--smoke-test"])

        self.assertEqual(result, 0)

    def test_real_auto_port_failure_is_not_replaced_with_a_guess(self):
        main, _, _ = launch_api(self)

        with patch(
            "tools.robot_cpg_gui.app.resolve_serial_port",
            side_effect=RuntimeError("no verified serial adapter; pass --port"),
        ):
            with self.assertRaisesRegex(RuntimeError, "verified serial"):
                main(["--smoke-test"])

    def test_auto_discovery_ignores_unverified_serial_devices(self):
        from tools.robot_cpg_gui.devices import serial_candidates

        ports = [
            SimpleNamespace(
                device="COM7", description="USB-SERIAL generic", manufacturer="Other",
                product="UART", hwid="USB VID:PID=9999:0001",
            ),
            SimpleNamespace(
                device="COM8", description="USB-SERIAL CH343", manufacturer="QinHeng",
                product="CH343", hwid="USB VID:PID=1A86:55D4",
            ),
        ]
        with patch("serial.tools.list_ports.comports", return_value=ports):
            self.assertEqual(serial_candidates(), ["COM8"])


if __name__ == "__main__":
    unittest.main()
