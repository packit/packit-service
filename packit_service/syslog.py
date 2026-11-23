# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging.handlers
import socket

from syslog_rfc5424_formatter import RFC5424Formatter


def create_syslog_handler(host: str, port: int, project: str):
    """Create a syslog handler only when its destination can be resolved."""
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
        handler = logging.handlers.SysLogHandler(address=(host, port))
    except OSError:
        return None

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RFC5424Formatter(msgid=project))
    return handler
