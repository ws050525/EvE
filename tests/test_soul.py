"""灵魂文件的测试：读、拼、记、忘、去重、上限。

不依赖模型；用临时目录，别碰真实的 soul/SOUL.md。
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.soul import Soul

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


def make(root: Path, body: str = ""):
    soul_path = root / "soul" / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(body, encoding="utf-8")
    cfg = SimpleNamespace(root=root, data_dir=root / ".data", soul_file="soul/SOUL.md")
    return Soul(cfg)


@case
def reads_the_handwritten_soul():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "- 叫我「你」\n- 「搜下 X」= 网易云搜 X")
        assert "搜下 X" in soul.text()
        block = soul.prompt_block()
        assert block.startswith("## 灵魂文件"), block[:40]
        assert "网易云搜 X" in block


@case
def missing_file_is_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "")
        (Path(tmp) / "soul" / "SOUL.md").unlink()
        assert soul.text() == ""
        assert soul.prompt_block() == ""
        assert soul.exists() is False


@case
def remember_appends_and_persists():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "## 我是谁\n")
        ok, message = soul.remember("他说「发我」时指发到微信文件传输助手")
        assert ok, message

        again = make(Path(tmp), "## 我是谁\n")
        assert any("发我" in fact for fact in again.learned()), again.learned()
        assert "发我" in again.prompt_block()


@case
def remember_rejects_short_and_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "")
        assert soul.remember("嗯")[0] is False
        assert soul.remember("他不喜欢反复确认，权限已经给过了")[0] is True
        assert soul.remember("他不喜欢反复确认，权限已经给过了")[0] is False
        assert len(soul.learned()) == 1


@case
def aprended_entries_are_capped():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "")
        for index in range(60):
            soul.remember(f"第 {index} 条长期偏好，内容要够长才能通过校验")
        from eve_agent.soul import MAX_LEARNED

        assert len(soul.learned()) <= MAX_LEARNED, len(soul.learned())


@case
def forget_by_keyword():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "")
        soul.remember("他说「搜下」指的是网易云搜歌")
        soul.remember("他不喜欢反复确认")
        assert soul.forget("搜下") == 1
        assert not any("搜下" in fact for fact in soul.learned())


@case
def prompt_block_can_be_trimmed_by_budget():
    with tempfile.TemporaryDirectory() as tmp:
        soul = make(Path(tmp), "长" * 5000)
        short = soul.prompt_block(chars=200)
        assert len(short) < 400, len(short)


@case
def soul_tools_round_trip():
    from eve_agent.tools.registry import ToolRegistry
    from eve_agent.tools.soul_tools import register_soul

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make(root, "- 「开 X」= 打开程序 X")
        cfg = SimpleNamespace(root=root, data_dir=root / ".data", soul_file="soul/SOUL.md")
        registry = ToolRegistry()
        register_soul(registry, cfg)
        allowed = set(registry.names())

        read_back = registry.dispatch("soul_read", {}, allowed=allowed)
        assert "开 X" in read_back, read_back

        saved = registry.dispatch("soul_remember", {"fact": "他说「测一下」要带证据回来"}, allowed=allowed)
        assert saved.startswith("[ok]"), saved
        assert "带证据" in registry.dispatch("soul_read", {}, allowed=allowed)

        removed = registry.dispatch("soul_forget", {"keyword": "带证据"}, allowed=allowed)
        assert removed.startswith("[ok]"), removed


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
