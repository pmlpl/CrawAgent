"""共享测试夹具：本地 HTTP 服务器。"""

import functools
import http.server
import threading

import pytest


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _make_handler(directory: str):
    return functools.partial(_SilentHandler, directory=directory)


@pytest.fixture()
def local_server(tmp_path):
    """启动一个静态文件 HTTP 服务器，返回 base_url。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    server.shutdown()
    server.server_close()
