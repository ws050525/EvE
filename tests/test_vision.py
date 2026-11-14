"""视觉工具的测试：模型挑选、图片编码、缺依赖时的报错。

真截图和真推理要开引擎，放在 e2e 里跑，这里只测不需要 GPU 的逻辑。
"""

import base64
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core import vision

CHECKS = []

# 1x1 的透明 PNG，够测编码
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def picks_a_model_that_has_a_vision_encoder():
    cfg = SimpleNamespace(
        models={
            "cloud": SimpleNamespace(mmproj=""),
            "local-plain": SimpleNamespace(mmproj=""),
            "qwen3vl-4b": SimpleNamespace(mmproj="models/mmproj.gguf"),
        }
    )
    assert vision.pick_vision_model(cfg) == "qwen3vl-4b"
    assert vision.pick_vision_model(cfg, "cloud") == "cloud"


@case
def returns_empty_when_nothing_can_see():
    cfg = SimpleNamespace(models={"a": SimpleNamespace(mmproj="")})
    assert vision.pick_vision_model(cfg) == ""


@case
def data_url_encodes_a_png():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.png"
        path.write_bytes(TINY_PNG)
        url = vision._data_url(path)
        assert url.startswith("data:image/png;base64,"), url[:40]
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == TINY_PNG


@case
def oversized_image_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "big.png"
        path.write_bytes(b"\x89PNG" + b"0" * (vision.MAX_BYTES + 10))
        try:
            vision._data_url(path)
        except RuntimeError as exc:
            assert "太大" in str(exc), exc
        else:
            raise AssertionError("超大图应该被挡住")


@case
def vision_tools_are_registered():
    from eve_agent.tools.registry import ToolRegistry
    from eve_agent.tools.vision_tools import register_vision

    registry = ToolRegistry()
    register_vision(registry, None, None)
    names = set(registry.names())
    assert {"screen_read", "image_read"} <= names, names


@case
def screen_read_without_runtime_fails_clearly():
    from eve_agent.tools.vision_tools import _screen_read

    out = _screen_read({"question": "有什么"}, None, None)
    assert out.startswith("[error]") and "runtime" in out, out


@case
def image_read_requires_an_existing_file():
    from eve_agent.tools.vision_tools import _image_read

    out = _image_read({"path": "D:/nope/不存在.png", "question": "x"}, None, None)
    assert out.startswith("[error]"), out


@case
def locate_maps_image_coordinates_back_to_the_screen():
    """只截活动窗口时，模型给的是"图上的像素"，要加上裁剪原点才是屏幕坐标。"""
    from PIL import Image

    from eve_core import vision

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shot.png"
        Image.new("RGB", (400, 300), "black").save(path)

        class FakeProvider:
            def chat(self, messages, tools=None, **kwargs):
                return SimpleNamespace(
                    content='{"found": true, "x": 100, "y": 200, "what": "输入框"}'
                )

        class FakeRuntime:
            def provider_for(self, model_id):
                return FakeProvider()

        cfg = SimpleNamespace(
            models={"v": SimpleNamespace(mmproj="models/mmproj.gguf")},
            vision_model="",
        )
        shot = vision.Shot(path=path, origin=(1000, 500), size=(400, 300), window="窗口")
        hit = vision.locate(FakeRuntime(), cfg, shot, "输入框")

        assert hit["found"] is True, hit
        # 400x300 没有触发缩放，所以就是图上坐标 + 原点
        assert hit["x"] == 1100 and hit["y"] == 700, hit
        assert shot.full_screen is False


@case
def fit_respects_the_pixel_budget():
    from PIL import Image

    from eve_core import vision

    big = Image.new("RGB", (2560, 1600), "black")
    fitted, scale = vision._fit(big)
    assert fitted.width * fitted.height <= vision.MAX_PIXELS * 1.02, fitted.size
    assert 0 < scale < 1
    assert max(fitted.width, fitted.height) <= vision.MAX_SIDE

    small = Image.new("RGB", (320, 240), "black")
    same, ratio = vision._fit(small)
    assert ratio == 1.0 and same.size == (320, 240)


@case
def capture_returns_geometry():
    """真截一张：默认裁活动窗口，拿不到窗口就退回全屏，两种情况都要有 origin。"""
    from eve_core import vision

    with tempfile.TemporaryDirectory() as tmp:
        shot = vision.capture(path=Path(tmp) / "a.png", folder=Path(tmp))
        assert shot.path.exists()
        assert shot.size[0] > 0 and shot.size[1] > 0
        assert isinstance(shot.origin, tuple) and len(shot.origin) == 2


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
