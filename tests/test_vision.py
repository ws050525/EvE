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
