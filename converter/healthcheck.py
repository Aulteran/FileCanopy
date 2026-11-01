"""Container health check: ask the server's own Unix socket for /healthz."""

import http.client
import os
import socket
import sys


class Conn(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(os.environ.get("CONVERTER_SOCKET", "/run/converter/converter.sock"))


conn = Conn("converter")
conn.request("GET", "/healthz")
sys.exit(0 if conn.getresponse().status == 200 else 1)
