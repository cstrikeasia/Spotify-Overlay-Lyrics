"""Live SSE browser source tests: no client polling, event-driven broadcast."""
from __future__ import annotations

import http.client
import json
from app.obs_source import ObsOverlayServer


def open_events(server: ObsOverlayServer):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    conn.request("GET", "/events")
    response = conn.getresponse()
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["Cache-Control"] == "no-cache, no-transform"
    assert "Content-Length" not in response.headers
    assert response.headers["Transfer-Encoding"] == "chunked"
    return conn, response


def read_frame(response):
    fields = {}
    while True:
        line = response.fp.readline().decode("utf-8")
        if not line:
            raise EOFError("SSE stream closed unexpectedly")
        if line in ("\n", "\r\n"):
            if fields:
                return fields
            continue
        if line.startswith(":"):
            fields["comment"] = line[1:].strip()
            continue
        key, _, value = line.partition(":")
        fields[key] = value.strip()


def read_state(response):
    for _ in range(5):
        frame = read_frame(response)
        if frame.get("event") == "state":
            return frame["id"], json.loads(frame["data"])
    raise AssertionError("Did not receive state event")


def test_stream_sends_initial_state_and_only_changed_snapshots():
    server = ObsOverlayServer(port=0)
    conn = None
    try:
        server.start()
        conn, response = open_events(server)
        first_id, first_state = read_state(response)
        assert first_state["kind"] == "missing"
        current = server.snapshot()
        current.update({"kind": "synced", "track_key": "a", "title": "第一首", "active_index": 0,
                        "lines": [{"index": 0, "text": "第一句"}]})
        server.publish(current)
        next_id, changed_state = read_state(response)
        assert int(next_id) == int(first_id) + 1
        assert changed_state["title"] == "第一首"
        assert changed_state["active_index"] == 0
        # 100 identical 120ms UI ticks do not create a single network event.
        for _ in range(100):
            server.publish(current)
        assert server._revision == int(next_id)
        current["active_index"] = 1
        current["lines"] = [{"index": 1, "text": "第二句"}]
        server.publish(current)
        third_id, next_state = read_state(response)
        assert int(third_id) == int(next_id) + 1
        assert next_state["lines"][0]["text"] == "第二句"
        current["title"] = "mutated after publish"
        assert server.snapshot()["title"] == "第一首"
    finally:
        if conn is not None:
            conn.close()
        server.stop()


def test_stream_pushes_to_multiple_obs_sources_and_reconnect_replays_latest():
    server = ObsOverlayServer(port=0)
    conns = []
    responses = []
    try:
        server.start()
        for _ in range(2):
            conn, response = open_events(server)
            conns.append(conn)
            responses.append(response)
            _, state = read_state(response)
            assert state["kind"] == "missing"
        next_state = server.snapshot()
        next_state.update({"track_key": "now", "title": "同一首歌曲", "kind": "synced"})
        server.publish(next_state)
        for response in responses:
            _, state = read_state(response)
            assert state["title"] == "同一首歌曲"
        # New subscribers immediately receive the latest state and not the
        # stale initial snapshot even when there is no new playback event.
        newcomer, fresh_response = open_events(server)
        conns.append(newcomer)
        _, fresh = read_state(fresh_response)
        assert fresh["title"] == "同一首歌曲"
        assert fresh["track_key"] == "now"
    finally:
        for conn in conns:
            conn.close()
        server.stop()


def test_sse_idle_heartbeat_is_not_an_update():
    server = ObsOverlayServer(port=0)
    server.HEARTBEAT_SECONDS = 0.15
    conn = None
    try:
        server.start()
        conn, response = open_events(server)
        read_state(response)
        frame = read_frame(response)
        assert frame.get("comment") == "keep-alive"
        assert "data" not in frame
        assert server._revision == 0
    finally:
        if conn is not None:
            conn.close()
        server.stop()


def test_event_endpoint_rejects_external_host_header():
    server = ObsOverlayServer(port=0)
    try:
        server.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
        try:
            conn.putrequest("GET", "/events", skip_host=True)
            conn.putheader("Host", "attacker.invalid")
            conn.endheaders()
            assert conn.getresponse().status == 403
        finally:
            conn.close()
    finally:
        server.stop()
