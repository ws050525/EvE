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


# ---------- 播报前的文本清洗 ----------


@case
def speech_cleaning_strips_markdown_and_kaomoji():
    from eve_core.tts import clean_for_speech

    messy = "好的**(・ω・)**\n\n- 第一步：`app_launch`\n- 看 [文档](https://example.com)"
    cleaned = clean_for_speech(messy)
    assert "*" not in cleaned and "`" not in cleaned, cleaned
    assert "・ω・" not in cleaned, cleaned
    assert "文档" in cleaned, cleaned
    assert "https://" not in cleaned, cleaned


@case
def speech_cleaning_drops_code_blocks():
    from eve_core.tts import clean_for_speech

    text = "先看这段代码\n```python\nprint(1)\n```\n然后跑一下"
    cleaned = clean_for_speech(text)
    assert "print" not in cleaned, cleaned
    assert "代码" in cleaned and "跑一下" in cleaned, cleaned


@case
def speech_cleaning_truncates_long_replies():
    from eve_core.tts import MAX_SPEECH_CHARS, clean_for_speech

    long_text = "这是一个很长的回复。" * 60
    cleaned = clean_for_speech(long_text)
    assert len(cleaned) <= MAX_SPEECH_CHARS + 1, len(cleaned)
    assert cleaned.endswith("。"), cleaned[-10:]


@case
def speech_cleaning_handles_empty():
    from eve_core.tts import clean_for_speech

    assert clean_for_speech("") == ""
    assert clean_for_speech("   ") == ""
    assert clean_for_speech(None) == ""


@case
def speech_cleaning_drops_list_bullets():
    from eve_core.tts import clean_for_speech

    text = "已经搞定了\n- 打开了记事本\n- 输入了文字\n1. 保存了文件"
    cleaned = clean_for_speech(text)
    assert "- " not in cleaned and "1. " not in cleaned, cleaned
    assert "打开了记事本" in cleaned and "保存了文件" in cleaned, cleaned


# ---------- 切句 ----------


@case
def split_never_cuts_inside_a_phrase():
    """播报"说一半"的根因：按字数硬切会切在词中间。现在必须落在标点上。"""
    from eve_core.tts import split_sentences

    text = "我已经帮你把文件整理好了，放在桌面上。你要是想再看一眼，我可以把改动列出来。"
    chunks = split_sentences(text)
    assert len(chunks) >= 2, chunks
    assert "".join(chunks) == text, chunks
    for chunk in chunks[:-1]:
        assert chunk[-1] in "。！？；，、：!?;,:\n…", f"这块没在标点处断开：{chunk!r}"


@case
def split_keeps_each_chunk_short():
    from eve_core.tts import split_sentences

    text = "第一点很长很长很长很长很长，第二点也很长很长很长很长，第三点还是长。"
    for chunk in split_sentences(text, max_chars=20):
        assert len(chunk) <= 20, (len(chunk), chunk)


@case
def split_merges_tiny_tail():
    from eve_core.tts import split_sentences

    chunks = split_sentences("这是第一句话内容比较长一些。嗯。")
    assert chunks[-1].endswith("嗯。"), chunks


@case
def split_handles_empty_and_short():
    from eve_core.tts import split_sentences

    assert split_sentences("") == []
    assert split_sentences("好") == ["好"]


@case
def split_survives_text_without_punctuation():
    from eve_core.tts import split_sentences

    text = "这一句故意没有任何标点符号所以只能硬切但也不能丢字"
    chunks = split_sentences(text)
    assert "".join(chunks) == text, chunks


# ---------- 播报参数 ----------


@case
def tts_parameters_clamp_to_sane_ranges():
    from eve_core.tts import PiperTts

    tts = PiperTts("D:/nope", speed=9.0, volume=9.0, noise_scale=9.0, noise_scale_w=-3.0)
    assert tts.speed == 2.0, tts.speed
    assert tts.volume == 1.5, tts.volume
    assert tts.noise_scale == 1.5, tts.noise_scale
    assert tts.noise_scale_w == 0.0, tts.noise_scale_w


@case
def tts_rejects_non_vits_directory():
    """指到 ASR / KWS 模型目录时要挡住——那种模型会在 C++ 层直接退出进程。"""
    import tempfile
    from pathlib import Path

    from eve_core.tts import PiperTts

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "sherpa-onnx-kws-fake"
        root.mkdir()
        (root / "tokens.txt").write_text("a 0\n", encoding="utf-8")
        (root / "encoder.onnx").write_bytes(b"x")
        tts = PiperTts(root)
        assert not tts.available
        assert "不是语音合成" in tts.error, tts.error


@case
def tts_settings_come_from_config():
    from eve_core.config import load_config

    cfg = load_config()
    voice = cfg.voice
    assert 0.5 <= voice.tts_speed <= 2.0, voice.tts_speed
    assert 0.0 <= voice.tts_volume <= 1.5, voice.tts_volume
    assert 0.0 <= voice.tts_noise_scale <= 1.5, voice.tts_noise_scale
    assert 0.0 <= voice.tts_noise_scale_w <= 1.5, voice.tts_noise_scale_w
    # 具体哪个音色会换，不写死；只要指向 models/voice 下的 vits 模型就行
    assert "vits" in Path(voice.tts_model).name, voice.tts_model


# ---------- 音色清单与偏好保存 ----------


@case
def list_voice_models_only_accepts_vits():
    import tempfile
    from pathlib import Path

    from eve_core.tts import list_voice_models

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "vits-fake"
        good.mkdir()
        (good / "tokens.txt").write_text("a 0\n", encoding="utf-8")
        (good / "lexicon.txt").write_text("a a\n", encoding="utf-8")
        (good / "fake.onnx").write_bytes(b"x")

        # 名字不像 vits 的（ASR / KWS）不能列出来
        other = root / "sherpa-onnx-kws-fake"
        other.mkdir()
        (other / "tokens.txt").write_text("a 0\n", encoding="utf-8")
        (other / "fake.onnx").write_bytes(b"x")

        # 没有词典的 piper 模型这版跑不了，也不该列出来
        piper = root / "vits-piper-fake"
        piper.mkdir()
        (piper / "tokens.txt").write_text("a 0\n", encoding="utf-8")
        (piper / "fake.onnx").write_bytes(b"x")

        voices = list_voice_models(root)
        assert [v["id"] for v in voices] == ["vits-fake"], voices
        assert voices[0]["label"], voices[0]


@case
def voice_preference_is_persisted_and_reloaded():
    import json
    import tempfile
    from pathlib import Path

    from eve_core.config import _apply_voice_overlay, save_voice_preference

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        save_voice_preference(
            data_dir, tts_model="D:/models/voice/vits-zh-hf-theresa", tts_speaker=2
        )
        # 不认识的字段要丢掉，不能污染配置
        save_voice_preference(data_dir, nonsense="x")

        raw = json.loads((data_dir / "voice.json").read_text(encoding="utf-8"))
        assert "nonsense" not in raw, raw

        loaded = _apply_voice_overlay(VoiceSettings(), data_dir)
        assert loaded.tts_model.endswith("theresa"), loaded.tts_model
        assert loaded.tts_speaker == 2, loaded.tts_speaker


@case
def missing_voice_overlay_leaves_settings_alone():
    import tempfile
    from pathlib import Path

    from eve_core.config import _apply_voice_overlay

    with tempfile.TemporaryDirectory() as tmp:
        base = VoiceSettings()
        loaded = _apply_voice_overlay(base, Path(tmp))
        assert loaded.tts_model == base.tts_model
        assert loaded.tts_speaker == base.tts_speaker


@case
def voice_fx_changes_but_does_not_clip():
    """后处理不能把波形搞爆，也不能改变长度。"""
    import numpy as np

    from eve_core.tts import VoiceFx, apply_voice_fx

    rate = 22050
    t = np.arange(rate) / rate
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")

    processed = apply_voice_fx(tone, rate, VoiceFx(enabled=True))
    assert len(processed) == len(tone), (len(processed), len(tone))
    assert float(np.abs(processed).max()) <= 1.0
    assert not np.allclose(processed, tone), "处理过就该和干声有区别"


@case
def voice_fx_can_be_disabled():
    import numpy as np

    from eve_core.tts import VoiceFx, apply_voice_fx

    tone = np.zeros(1000, dtype="float32")
    out = apply_voice_fx(tone, 22050, VoiceFx(enabled=False))
    assert out is tone or np.array_equal(out, tone)


@case
def voice_fx_survives_tiny_input():
    import numpy as np

    from eve_core.tts import VoiceFx, apply_voice_fx

    out = apply_voice_fx(np.zeros(0, dtype="float32"), 22050, VoiceFx())
    assert len(out) == 0


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
