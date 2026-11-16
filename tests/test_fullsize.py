"""全图导出测试：原图字节不变、衍生图明确标识、危险 UUID 不可穿目录。"""
from pathlib import Path

from PIL import Image

from birdscan.fullsize import export_full


def test_export_original_jpeg_preserves_bytes_and_dimensions(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (1600, 1200), (30, 120, 200)).save(source, quality=91)
    expected = source.read_bytes()

    out = export_full("ABC-123", source, "original", full_dir=tmp_path / "full")

    assert out["kind"] == "original"
    assert Path(out["path"]).read_bytes() == expected
    assert (out["width"], out["height"]) == (1600, 1200)
    with Image.open(out["path"]) as im:
        assert im.size == (1600, 1200)


def test_export_derivative_is_not_labelled_original(tmp_path):
    source = tmp_path / "derivative.jpeg"
    Image.new("RGB", (1024, 768), (200, 30, 30)).save(source)

    out = export_full("DEF-456", source, "derivative", full_dir=tmp_path / "full")

    assert out["kind"] == "derivative"
    assert Path(out["path"]).exists()
    assert (out["width"], out["height"]) == (1024, 768)


def test_export_png_is_converted_to_complete_jpeg(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(source)
    out = export_full("PNG-1", source, "original", full_dir=tmp_path / "full")
    assert Path(out["path"]).exists()
    assert not Path(out["path"]).with_name(".PNG-1.tmp.jpg").exists()
    with Image.open(out["path"]) as im:
        assert im.format == "JPEG" and im.size == (800, 600)


def test_export_hashes_unsafe_asset_uuid(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (20, 20)).save(source)
    out = export_full("VIDEO#f0", source, "frame", full_dir=tmp_path / "full")
    name = Path(out["path"]).name
    assert name.startswith("asset-") and name.endswith(".jpg")
    assert "#" not in name and ".." not in name
    assert Path(out["path"]).parent == tmp_path / "full"
