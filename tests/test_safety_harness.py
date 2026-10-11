"""The deny-by-default fixture in conftest.py really denies, and can be lifted."""

import socket
import subprocess
import sys

import pytest

from conftest import ForbiddenInTests


def test_a_connection_off_this_machine_is_refused():
    with pytest.raises(ForbiddenInTests):
        socket.create_connection(("192.0.2.1", 80), timeout=1)


def test_a_name_other_than_localhost_is_not_looked_up():
    with pytest.raises(ForbiddenInTests):
        socket.getaddrinfo("example.com", 443)


def test_loopback_still_works():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()


def test_starting_a_program_is_refused():
    with pytest.raises(ForbiddenInTests):
        subprocess.run([sys.executable, "-c", "pass"])


def test_a_shell_command_is_refused():
    import os
    with pytest.raises(ForbiddenInTests):
        os.system("true")


@pytest.mark.allow_process
def test_the_process_marker_lifts_the_rule():
    assert subprocess.run([sys.executable, "-c", "pass"]).returncode == 0
