from pathlib import Path

from matcreator.tools.util_tools import show_plot


def test_external_plot_is_published_without_changing_source(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(workspace))
    source = tmp_path / "parity.png"
    content = b"\x89PNG\r\n\x1a\nplot-test"
    source.write_bytes(content)
    first = show_plot(str(source))
    published = Path(first["plot_path"])
    assert published.is_relative_to(workspace)
    assert published.read_bytes() == source.read_bytes() == content
    assert show_plot(str(source)) == first
    source.write_bytes(content + b"updated")
    assert show_plot(str(source)) != first


def test_relative_plot_uses_configured_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    source = tmp_path / "parity.png"
    source.write_bytes(b"plot-test")
    assert show_plot("parity.png") == {"plot_path": str(source.resolve())}
    assert "error" in show_plot("missing.png")


def test_non_image_external_file_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path / "workspace"))
    source = tmp_path / "config.yaml"
    source.write_text("private: value")
    assert "error" in show_plot(str(source))
    assert not (tmp_path / "workspace").exists()
