"""Command line tests.

The one that matters is test_systemd_unit_command_parses: the systemd unit
invokes `divoom_pi run --config /etc/divoom-pi/config.ini`, and argparse does
not accept a top-level option after a subcommand. That mismatch made every
single service start fail with "unrecognized arguments" while the CLI looked
fine by hand, so the unit's own command line is now checked against the real
parser.
"""

import contextlib
import io
import os
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from divoom_pi import cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT = os.path.join(ROOT, "systemd", "divoom-pi.service")

# What install.sh substitutes into the unit.
SUBSTITUTIONS = {
    "@PREFIX@": "/opt/divoom-pi",
    "@CONFIG@": "/etc/divoom-pi/config.ini",
    "@PYTHON@": "/usr/bin/python3",
}


def unit_exec_start():
    with open(UNIT, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("ExecStart="):
                command = line.split("=", 1)[1].strip()
                for placeholder, value in SUBSTITUTIONS.items():
                    command = command.replace(placeholder, value)
                return shlex.split(command)
    raise AssertionError("no ExecStart in %s" % UNIT)


class SystemdUnitTest(unittest.TestCase):
    def test_unit_has_no_leftover_placeholders(self):
        command = unit_exec_start()
        for word in command:
            self.assertNotIn("@", word, "unsubstituted placeholder in ExecStart")

    def test_systemd_unit_command_parses(self):
        command = unit_exec_start()
        self.assertIn("-m", command)
        self.assertEqual(command[command.index("-m") + 1], "divoom_pi")

        arguments = command[command.index("-m") + 2 :]
        args = cli.build_parser().parse_args(arguments)

        self.assertIs(args.func, cli.command_run)
        self.assertEqual(args.config, SUBSTITUTIONS["@CONFIG@"])

    def test_sandboxing_leaves_the_avahi_directory_writable(self):
        # ProtectSystem=full mounts /etc read-only too, which stopped the
        # gateway publishing any mDNS record - silently, because it is only a
        # warning in its own log. Whatever the sandboxing, that one directory
        # has to stay writable.
        with open(UNIT, encoding="utf-8") as handle:
            text = handle.read()
        if "ProtectSystem=full" in text or "ProtectSystem=strict" in text:
            self.assertIn("ReadWritePaths=/etc/avahi/services", text)

    def test_unit_gives_up_on_permanent_failures(self):
        # Exit code 2 means bad usage or an unsupported platform; restarting
        # forever just burns a Pi Zero's CPU, as 59 restarts in a row proved.
        with open(UNIT, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("RestartPreventExitStatus=2", text)
        self.assertIn("StartLimitBurst=", text)


class GlobalOptionPlacementTest(unittest.TestCase):
    """--config and --log-level have to work on either side of the subcommand."""

    def parse(self, command):
        return cli.build_parser().parse_args(shlex.split(command))

    def test_config_after_the_subcommand(self):
        args = self.parse("run --config /tmp/x.ini")
        self.assertEqual(args.config, "/tmp/x.ini")
        self.assertIs(args.func, cli.command_run)

    def test_config_before_the_subcommand(self):
        args = self.parse("--config /tmp/x.ini run")
        self.assertEqual(args.config, "/tmp/x.ini")
        self.assertIs(args.func, cli.command_run)

    def test_log_level_after_the_subcommand(self):
        # What docs/TROUBLESHOOTING.md tells people to run.
        args = self.parse("run --log-level DEBUG")
        self.assertEqual(args.log_level, "DEBUG")

    def test_every_subcommand_accepts_the_global_options(self):
        for command in (
            "run",
            "scan",
            "doctor",
            "pair AA:BB:CC:DD:EE:FF",
            "selftest AA:BB:CC:DD:EE:FF",
        ):
            with self.subTest(command=command):
                args = self.parse("%s --config /tmp/x.ini --log-level DEBUG" % command)
                self.assertEqual(args.config, "/tmp/x.ini")
                self.assertEqual(args.log_level, "DEBUG")

    def test_unset_options_stay_unset(self):
        # They use default=SUPPRESS so the subparser cannot clobber a value the
        # main parser already read, which means they may be absent entirely.
        args = self.parse("run")
        self.assertIsNone(getattr(args, "config", None))
        self.assertIsNone(getattr(args, "log_level", None))
        self.assertEqual(cli.log_level_of(args), "INFO")

    def test_both_positions_actually_load_the_file(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8")
        handle.write("[gateway]\nport = 7788\nmax_clients = 9\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        for command in ("run --config %s", "--config %s run"):
            with self.subTest(command=command):
                config = cli.load_config(self.parse(command % shlex.quote(handle.name)))
                self.assertEqual(config.path, handle.name)
                self.assertEqual(config.port, 7788)
                self.assertEqual(config.max_clients, 9)


class RunCommandOptionsTest(unittest.TestCase):
    def test_listen_and_port_overrides(self):
        args = cli.build_parser().parse_args(["run", "--listen", "127.0.0.1", "--port", "9999"])
        config = cli.load_config(args)
        self.assertEqual(config.listen, "127.0.0.1")
        self.assertEqual(config.port, 9999)

    def test_missing_config_file_falls_back_to_defaults(self):
        args = cli.build_parser().parse_args(["run", "--config", "/nonexistent/divoom-pi.ini"])
        config = cli.load_config(args)
        self.assertEqual(config.port, 7777)
        self.assertIsNone(config.path)


class DoctorAvahiCheckTest(unittest.TestCase):
    """/etc/avahi/services is root-owned and the gateway runs as root.

    An unprivileged `divoom-pi doctor` therefore cannot tell whether the
    service can write there, and reporting that as a failure sent a user
    chasing a problem they did not have.
    """

    def run_doctor(self, *, euid, published, available):
        args = cli.build_parser().parse_args(["doctor"])
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(cli.os, "geteuid", lambda: euid, create=True))
            stack.enter_context(mock.patch("divoom_pi.cli.os.path.isdir", return_value=True))
            stack.enter_context(mock.patch("divoom_pi.cli.os.listdir", return_value=published))
            stack.enter_context(
                mock.patch.object(cli.AvahiPublisher, "available", lambda self: available)
            )
            stack.enter_context(mock.patch("divoom_pi.cli.shutil.which", return_value=None))
            stack.enter_context(mock.patch("divoom_pi.cli._port_open", return_value=False))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.command_doctor(args)
        return output.getvalue()

    def avahi_line(self, output):
        for line in output.splitlines():
            if "avahi service directory" in line:
                return line
        self.fail("no avahi line in: " + output)

    def test_unprivileged_with_no_records_warns_rather_than_fails(self):
        output = self.run_doctor(
            euid=1000, published=[], available="/etc/avahi/services is not writable"
        )
        line = self.avahi_line(output)
        self.assertIn("[warn]", line)
        self.assertIn("sudo", line)
        self.assertIn("not root", output)

    def test_existing_records_pass_even_unprivileged(self):
        output = self.run_doctor(
            euid=1000,
            published=["divoom-pi-b12181bfa8eb.service"],
            available="/etc/avahi/services is not writable",
        )
        self.assertIn("[ ok ]", self.avahi_line(output))

    def test_root_still_reports_a_real_failure(self):
        output = self.run_doctor(
            euid=0, published=[], available="/etc/avahi/services is not writable"
        )
        self.assertIn("[FAIL]", self.avahi_line(output))
        self.assertNotIn("not root", output)


if __name__ == "__main__":
    unittest.main()
