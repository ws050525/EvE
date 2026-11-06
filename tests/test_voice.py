"""语音模块的单元测试：唤醒词判定 + 断句状态机。

不需要麦克风，也不需要模型——纯逻辑。
识别那条链路单独用模型自带的示例音频验（脚本见 .tmp/verify_voice.py）。

跑法： & 'D:\\python\\python.exe' tests\\test_voice.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core.voice import (
    AudioConfig,
    EnergyVad,
    Recorder,
    SherpaRecognizer,
    VoiceSettings,
    match_wake,
    mode_aliases,
    normalize,
    parse_mode_command,
    wake_variants,
)

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


# ---------- 唤醒词 ----------


@case
def normalize_strips_punctuation_and_case():
    assert normalize("伊芙，打开 记事本！") == "伊芙打开记事本"
    assert normalize(" eve hello ") == "evehello"
    assert normalize("") == ""


@case
def variants_cover_common_mishearing():
    variants = wake_variants("伊芙")
    for expected in ("伊芙", "伊弗", "衣芙", "伊夫", "eve", "小伊"):
        assert expected in variants, (expected, variants)


@case
def wake_at_sentence_start_only():
    hit, rest = match_wake("伊芙，打开记事本")
    assert hit and rest == "打开记事本", (hit, rest)

    # 句子中间提到「伊芙」不算唤醒，否则聊天里提一嘴就误触发
    hit, rest = match_wake("刚才伊芙说了什么")
    assert not hit, (hit, rest)
    assert rest == "刚才伊芙说了什么"


@case
def wake_accepts_variants_and_keeps_case():
    hit, rest = match_wake("小伊打开微信")
    assert hit and rest == "打开微信", (hit, rest)

    hit, rest = match_wake("EVE Open Notepad")
    assert hit and rest == "Open Notepad", (hit, rest)


@case
def wake_with_only_the_word_leaves_nothing():
    hit, rest = match_wake("伊芙。")
    assert hit and rest == "", (hit, rest)


@case
def wake_ignores_empty_input():
    assert match_wake("") == (False, "")
    assert match_wake("   ") == (False, "")


@case
def custom_wake_word_works():
    hit, rest = match_wake("小爱，开灯", "小爱")
    assert hit and rest == "开灯", (hit, rest)

    # 唤醒词必须出现在句首
    hit, rest = match_wake("帮我叫一下小爱", "小爱")
    assert not hit, (hit, rest)


# ---------- 语音切档 ----------


LABELS = {"chat": "聊天", "desktop": "桌面", "work": "工作"}


@case
def mode_switch_accepts_common_phrasings():
    aliases = mode_aliases(LABELS)
    cases = {
        "切换到工作": "work",
        "切到桌面": "desktop",
        "换到聊天": "chat",
        "进入工作模式": "work",
        "工作模式": "work",
        "桌面档": "desktop",
        "聊天": "chat",
        "用工作": "work",
        "work": "work",
        "干活": "work",
    }
    for text, expected in cases.items():
        got = parse_mode_command(text, aliases)
        assert got == expected, f"{text!r} -> {got!r}，期望 {expected!r}"


@case
def mode_switch_ignores_normal_speech():
    aliases = mode_aliases(LABELS)
    for text in (
        "打开记事本",
        "我在想工作的事",
        "帮我写一个工作周报",
        "今天聊点什么好呢",
        "",
        "   ",
    ):
        got = parse_mode_command(text, aliases)
        assert got is None, f"{text!r} 不该被当成切档，得到 {got!r}"


# ---------- 断句 ----------


def quiet_block() -> float:
    return 0.001


def speech_block() -> float:
    return 0.08


@case
def vad_never_ends_on_pure_silence():
    cfg = AudioConfig(silence_ms=200, min_speech_ms=100, block_ms=32)
    vad = EnergyVad(cfg)
    for _ in range(200):
        assert not vad.update(quiet_block())
    assert not vad.heard_speech


@case
def vad_ends_after_speech_then_silence():
    cfg = AudioConfig(silence_ms=200, min_speech_ms=100, block_ms=32)
    vad = EnergyVad(cfg)
    for _ in range(10):  # 先校准环境噪音
        vad.update(quiet_block())
    ended = False
    for _ in range(10):  # 说 320ms
        ended = vad.update(speech_block())
    assert vad.heard_speech
    assert not ended, "还在说的时候不该收尾"

    for _ in range(20):  # 静音 640ms
        ended = vad.update(quiet_block())
    assert ended, "说完静音够久应该收尾"


@case
def vad_ignores_short_blips():
    """敲键盘那种 30ms 的响动不该被当成一句话。"""
    cfg = AudioConfig(silence_ms=100, min_speech_ms=240, block_ms=32)
    vad = EnergyVad(cfg)
    for _ in range(10):
        vad.update(quiet_block())
    finished = False
    for _ in range(30):
        finished = vad.update(speech_block() if _ == 0 else quiet_block())
    assert not finished
    assert not vad.heard_speech


@case
def vad_raises_threshold_in_noisy_room():
    cfg = AudioConfig(silence_ms=100, min_speech_ms=100, block_ms=32, energy_floor=0.01)
    quiet = EnergyVad(AudioConfig(silence_ms=100, min_speech_ms=100, block_ms=32, energy_floor=0.01))
    for _ in range(8):
        quiet.update(0.001)

    noisy = EnergyVad(cfg)
    for _ in range(8):
        noisy.update(0.05)  # 环境本身就吵

    assert noisy.threshold() > quiet.threshold(), (noisy.threshold(), quiet.threshold())


@case
def vad_rms_matches_known_values():
    assert abs(EnergyVad.rms([1.0, 1.0, 1.0, 1.0]) - 1.0) < 1e-9
    assert abs(EnergyVad.rms([0.0, 0.0]) - 0.0) < 1e-9
    assert EnergyVad.rms([]) == 0.0


@case
def block_size_follows_sample_rate():
    assert AudioConfig(sample_rate=16000, block_ms=32).block_size == 512
    assert AudioConfig(sample_rate=16000, block_ms=20).block_size == 320


# ---------- 后端可用性 ----------


@case
def recorder_does_not_crash_when_probing():
    recorder = Recorder(AudioConfig())
    assert isinstance(recorder.available, bool)


@case
def recognizer_reports_missing_model():
    recognizer = SherpaRecognizer("D:/definitely/not/here")
    assert not recognizer.available
    assert "不存在" in recognizer.error


@case
def settings_defaults_are_sane():
    settings = VoiceSettings()
    assert settings.wake_word == "伊芙"
    assert settings.silence_ms > 0
    assert settings.max_seconds > 0


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
