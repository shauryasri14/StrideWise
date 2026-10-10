"""The map download must never hand Valhalla an error page or a half-finished file."""
import hashlib
import http.server
import importlib.util
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("setup_region_dl", ROOT / "scripts" / "setup_region.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)

# A fake "map": real files start with a header block of type OSMHeader, and are much bigger than an error page.
FAKE = b"\x00\x00\x00\x0d\x0a\x09OSMHeader\x18\x20" + bytes(range(256)) * 600
MD5 = hashlib.md5(FAKE).hexdigest()
PATH = "/asia/map-latest.osm.pbf"
ERROR_PAGE = b"<html><body>" + b"x" * 252 + b"</body></html>"  # 278 bytes, like the one in the bug report


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)


def serve(routes):
    hits = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits[self.path] = hits.get(self.path, 0) + 1
            fn = routes.get(self.path)
            if fn is None:
                self.send_response(404); self.end_headers(); return
            fn(self, hits[self.path])

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", hits


def body(data, ctype="application/octet-stream"):
    def fn(h, n):
        rng = h.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            if start >= len(data):
                h.send_response(416); h.end_headers(); return
            chunk = data[start:]
            h.send_response(206)
            h.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            chunk = data
            h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(chunk)))
        h.end_headers()
        h.wfile.write(chunk)
    return fn


def status(code, retry_after=None):
    def fn(h, n):
        h.send_response(code)
        if retry_after is not None:
            h.send_header("Retry-After", str(retry_after))
        h.send_header("Content-Length", "0")
        h.end_headers()
    return fn


def good_routes():
    return {PATH: body(FAKE), PATH + ".md5": body(f"{MD5}  map-latest.osm.pbf\n".encode(), "text/plain")}


def test_downloads_and_verifies_checksum(tmp_path):
    srv, base, _ = serve(good_routes())
    path, checked = sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert path.read_bytes() == FAKE and checked is True
    assert not list(tmp_path.glob("*.part"))


def test_works_without_a_checksum_file(tmp_path):
    srv, base, _ = serve({PATH: body(FAKE)})
    path, checked = sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert path.read_bytes() == FAKE and checked is False


def test_html_block_page_is_rejected(tmp_path):
    srv, base, _ = serve({PATH: body(ERROR_PAGE, "text/html")})
    with pytest.raises(sr.FatalDownloadError, match="web page"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert not (tmp_path / "map-latest.osm.pbf").exists()


def test_278_byte_error_body_is_never_kept_as_the_map(tmp_path):
    """The exact bug: a tiny error body saved under the map's name made Valhalla crash with 'unable to read blob'."""
    srv, base, _ = serve({PATH: body(ERROR_PAGE)})
    with pytest.raises(sr.FatalDownloadError, match="278 bytes"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert not (tmp_path / "map-latest.osm.pbf").exists() and not list(tmp_path.glob("*.part"))


def test_resumes_after_dropped_connection(tmp_path):
    def flaky(h, n):
        if n == 1:  # promise the full file, send half, hang up
            h.send_response(200)
            h.send_header("Content-Type", "application/octet-stream")
            h.send_header("Content-Length", str(len(FAKE)))
            h.end_headers()
            h.wfile.write(FAKE[: len(FAKE) // 2])
            h.wfile.flush()
            h.close_connection = True
        else:
            body(FAKE)(h, n)
    routes = good_routes(); routes[PATH] = flaky
    srv, base, hits = serve(routes)
    path, checked = sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert path.read_bytes() == FAKE and checked and hits[PATH] == 2


def test_rate_limit_is_retried(tmp_path):
    first = {"n": 0}

    def limited(h, n):
        (status(429, 0) if n == 1 else body(FAKE))(h, n)
    routes = good_routes(); routes[PATH] = limited
    srv, base, hits = serve(routes)
    path, _ = sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert path.read_bytes() == FAKE and hits[PATH] == 2


def test_gives_up_after_repeated_server_errors(tmp_path):
    srv, base, hits = serve({PATH: status(503)})
    with pytest.raises(sr.FatalDownloadError, match="giving up after 3 attempts"):
        sr.download_pbf(base + PATH, tmp_path, retries=3)
    srv.shutdown()
    assert hits[PATH] == 3


def test_404_is_fatal_and_explains(tmp_path):
    srv, base, _ = serve({})
    with pytest.raises(sr.FatalDownloadError, match="404"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()


def test_403_is_fatal_and_mentions_block(tmp_path):
    srv, base, _ = serve({PATH: status(403)})
    with pytest.raises(sr.FatalDownloadError, match="refused"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()


def test_existing_bad_file_is_replaced(tmp_path):
    (tmp_path / "map-latest.osm.pbf").write_bytes(ERROR_PAGE)
    srv, base, _ = serve(good_routes())
    path, _ = sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert path.read_bytes() == FAKE


def test_existing_good_file_is_not_downloaded_again(tmp_path):
    (tmp_path / "map-latest.osm.pbf").write_bytes(FAKE)
    srv, base, hits = serve(good_routes())
    sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert PATH not in hits  # only the small .md5 file was fetched


def test_wrong_checksum_is_rejected(tmp_path):
    routes = good_routes(); routes[PATH + ".md5"] = body(b"0" * 32 + b"  map-latest.osm.pbf\n", "text/plain")
    srv, base, _ = serve(routes)
    with pytest.raises(sr.FatalDownloadError, match="checksum"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()
    assert not (tmp_path / "map-latest.osm.pbf").exists()


def test_not_enough_disk_space(tmp_path, monkeypatch):
    class Usage:
        free = 1024
    monkeypatch.setattr(sr.shutil, "disk_usage", lambda p: Usage)
    srv, base, _ = serve(good_routes())
    with pytest.raises(sr.FatalDownloadError, match="not enough free disk space"):
        sr.download_pbf(base + PATH, tmp_path)
    srv.shutdown()


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ENV", tmp_path / ".env"); monkeypatch.setattr(sr, "ENV_EXAMPLE", tmp_path / "none")
    monkeypatch.setattr(sr, "DATA", tmp_path / "data")


def test_main_downloads_map_for_chosen_region(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    srv, base, _ = serve(good_routes())
    monkeypatch.setattr(sys, "argv", ["setup_region.py", "--pbf-url", base + PATH, "--yes"])
    sr.main()
    srv.shutdown()
    assert (tmp_path / "data" / "valhalla" / "map-latest.osm.pbf").read_bytes() == FAKE
    assert f"VALHALLA_PBF_URL={base + PATH}" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "checksum verified" in capsys.readouterr().out


def test_main_download_only_uses_env_and_fixes_a_bad_file(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    srv, base, _ = serve(good_routes())
    (tmp_path / ".env").write_text(f"VALHALLA_PBF_URL={base + PATH}\n", encoding="utf-8")
    bad = tmp_path / "data" / "valhalla" / "map-latest.osm.pbf"
    bad.parent.mkdir(parents=True); bad.write_bytes(ERROR_PAGE)
    monkeypatch.setattr(sys, "argv", ["setup_region.py", "--download-only"])
    sr.main()
    srv.shutdown()
    assert bad.read_bytes() == FAKE


def test_main_failure_prints_manual_steps_and_exits(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    srv, base, _ = serve({PATH: status(403)})
    monkeypatch.setattr(sys, "argv", ["setup_region.py", "--pbf-url", base + PATH, "--yes"])
    with pytest.raises(SystemExit) as e:
        sr.main()
    srv.shutdown()
    msg = str(e.value)
    assert "browser" in msg and "map-latest.osm.pbf" in msg and "--download-only" in msg


def test_download_only_without_a_region_explains(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["setup_region.py", "--download-only"])
    with pytest.raises(SystemExit, match="setup_region.py"):
        sr.main()


def test_ssl_failure_gives_actionable_message(monkeypatch):
    import ssl, urllib.error, urllib.request

    def boom(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as e:
        sr._open(urllib.request.Request("https://example.com"), 5)
    assert "Install Certificates" in str(e.value)


def test_cached_index_with_non_ascii_names_loads(tmp_path, monkeypatch):
    import json
    cache = tmp_path / "idx.json"
    cache.write_bytes(json.dumps({"features": [{"properties": {"name": "Bāṅglā Ōsaka"}}]}, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(sr, "INDEX_CACHE", cache)
    assert sr.load_index()["features"][0]["properties"]["name"] == "Bāṅglā Ōsaka"


def test_blocked_by_proxy_is_not_retried(tmp_path, monkeypatch):
    import urllib.error, urllib.request
    calls = []

    def blocked(*a, **k):
        calls.append(1)
        raise urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    with pytest.raises(sr.FatalDownloadError, match="proxy or firewall"):
        sr.download_pbf("https://download.geofabrik.de/asia/map-latest.osm.pbf", tmp_path)
    assert len(calls) == 1
