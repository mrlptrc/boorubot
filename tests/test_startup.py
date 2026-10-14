"""Início automático (.vbs na pasta Inicializar) e instância única do painel, sem depender de Windows."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from boorubot import cli, startup


def test_vbs_runs_panel_hidden_from_project_dir():
    text = startup.vbs_content(r"C:\Users\Ana Maria\.local\bin\uv.exe", Path(r"C:\code\post automation"), 8765)
    assert 'sh.CurrentDirectory = "C:\\code\\post automation"' in text
    # caminho com espaço vai entre aspas duplicadas (regra de string do VBScript)
    assert 'sh.Run """C:\\Users\\Ana Maria\\.local\\bin\\uv.exe"" run boorubot panel --no-browser --port 8765", 0, False' in text
    assert text.endswith("\r\n")


def test_vbs_escapes_quotes_in_paths():
    text = startup.vbs_content(r'C:\a"b\uv.exe', Path(r'C:\x"y'))
    assert 'sh.CurrentDirectory = "C:\\x""y"' in text
    assert '"""C:\\a""b\\uv.exe""' in text


def test_install_status_remove(tmp_path):
    uv = tmp_path / "uv.exe"
    uv.write_text("")
    folder = tmp_path / "Startup"

    assert not startup.status(folder).installed
    path = startup.install(folder, uv=str(uv), project_dir=tmp_path / "proj")
    assert path == folder / startup.SCRIPT_NAME
    st = startup.status(folder)
    assert st.installed and st.uv == str(uv)
    assert startup.remove(folder) is True
    assert startup.remove(folder) is False
    assert not startup.status(folder).installed


def test_install_records_resolved_absolute_uv(tmp_path, monkeypatch):
    uv = tmp_path / "bin" / "uv.exe"
    uv.parent.mkdir()
    uv.write_text("")
    monkeypatch.setenv("UV", str(uv))
    path = startup.install(tmp_path / "Startup")
    assert str(uv.resolve()) in path.read_text(encoding="utf-16")


def test_missing_uv_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(startup.shutil, "which", lambda _: None)
    with pytest.raises(startup.StartupError, match="uv"):
        startup.resolve_uv()


def test_not_windows_is_unsupported(monkeypatch):
    monkeypatch.setattr(startup, "startup_dir", lambda: None)
    assert startup.status().supported is False
    with pytest.raises(startup.StartupError):
        startup.install()


# --- instância única -----------------------------------------------------------

def serve(body: bytes, status: int = 200):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port() -> int:
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    return port


def test_detects_running_panel():
    server = serve(b'{"jobs": []}')
    try:
        assert cli.panel_running("127.0.0.1", server.server_port)
    finally:
        server.shutdown()


def test_other_program_on_port_is_not_a_panel():
    server = serve(b"nope", status=404)
    try:
        port = server.server_port
        assert not cli.panel_running("127.0.0.1", port)
        assert cli.port_in_use("127.0.0.1", port)
    finally:
        server.shutdown()


def test_free_port():
    port = free_port()
    assert not cli.panel_running("127.0.0.1", port)
    assert not cli.port_in_use("127.0.0.1", port)


def test_second_panel_only_opens_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "panel_running", lambda host, port: True)
    monkeypatch.setattr("webbrowser.open", opened.append)
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("não pode subir outro painel"))

    assert cli.main(["panel", "--port", "8765"]) == 0
    assert opened == ["http://127.0.0.1:8765"]
    assert cli.main(["panel", "--no-browser"]) == 0
    assert len(opened) == 1


def test_port_taken_by_other_program_fails(monkeypatch):
    monkeypatch.setattr(cli, "panel_running", lambda host, port: False)
    monkeypatch.setattr(cli, "port_in_use", lambda host, port: True)
    assert cli.main(["panel", "--no-browser"]) == 1


def test_written_file_has_single_crlf(tmp_path):
    uv = tmp_path / "uv.exe"
    uv.write_text("")
    path = startup.install(tmp_path / "Startup", uv=str(uv), project_dir=tmp_path)
    raw = path.read_bytes().decode("utf-16")
    assert "\r\r\n" not in raw and raw.count("\r\n") == 5
