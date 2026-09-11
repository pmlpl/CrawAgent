"""访问日志过滤器：抑制 ContextRing 每 3 秒 /context 轮询的 200 OK 刷屏。"""
import logging

from crawagent.web.server import _ContextPollAccessFilter


def _record(msg, *args):
    r = logging.LogRecord("uvicorn.access", logging.INFO, "?", 0, msg, args, None, "?")
    return r


def test_filter_drops_context_poll_get():
    f = _ContextPollAccessFilter()
    # uvicorn 访问日志格式：'%s - "%s %s HTTP/%s" %d %s'，args 形如
    r = _record(
        '%s - "%s %s HTTP/%s" %d %s',
        "127.0.0.1:53266", "GET", "/api/sessions/3935718fc960/context", "1.1", 200, "OK",
    )
    assert f.filter(r) is False


def test_filter_keeps_other_session_endpoints():
    f = _ContextPollAccessFilter()
    r = _record(
        '%s - "%s %s HTTP/%s" %d %s',
        "127.0.0.1:53266", "GET", "/api/sessions/3935718fc960/metrics", "1.1", 200, "OK",
    )
    assert f.filter(r) is True


def test_filter_keeps_non_get_context():
    f = _ContextPollAccessFilter()
    # 非 GET（如 POST）不该误杀
    r = _record(
        '%s - "%s %s HTTP/%s" %d %s',
        "127.0.0.1:53266", "POST", "/api/sessions/abc/context", "1.1", 200, "OK",
    )
    assert f.filter(r) is True


def test_filter_keeps_non_session_gets():
    f = _ContextPollAccessFilter()
    r = _record(
        '%s - "%s %s HTTP/%s" %d %s',
        "127.0.0.1:53266", "GET", "/api/settings", "1.1", 200, "OK",
    )
    assert f.filter(r) is True


def test_filter_handles_malformed_record():
    f = _ContextPollAccessFilter()
    # 无 args / getMessage 异常时不抛、放行
    r = logging.LogRecord("uvicorn.access", logging.INFO, "?", 0, "not an access line", None, None, "?")
    assert f.filter(r) is True
