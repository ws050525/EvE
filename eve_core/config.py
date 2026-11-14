"""配置加载。

v0.4 起，"模型"是一等公民：
- `[models.*]` 声明有哪些模型（本地 GGUF 或云端 API）
- `[modes.*]` 只声明"用哪个 model_id"，不再区分 local / cloud
- `[engines.*]` 声明推理引擎本体（可执行文件、线程数、额外参数）

这样换本地模型、换云端厂商、甚至给同一个档位换成本地模型，都只是改数据。
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .voice import VoiceSettings

VOICE_OVERLAY = "voice.json"


def _default_voice() -> VoiceSettings:
    return VoiceSettings()


class ConfigError(RuntimeError):
    """配置有问题时抛出，消息面向用户。"""


# ---------- 内部：给 Provider 用的连接参数 ----------


@dataclass
class ProviderConfig:
    name: str
    type: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    timeout: float = 300.0
    enabled: bool = True
    stream_usage: bool = False  # 云端支持 stream_options.include_usage；本地引擎不一定

    def resolved_key(self) -> str:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env, "").strip()
            if not value:
                raise ConfigError(
                    f"模型 '{self.name}' 需要环境变量 {self.api_key_env}，当前为空"
                )
            return value
        return self.api_key or "not-needed"


# ---------- 模型 ----------


@dataclass
class ModelConfig:
    id: str
    label: str
    kind: str = ""  # local_gguf | cloud_api（留空则按 base_url 推断）
    engine: str = "llamacpp"
    path: str = ""
    mmproj: str = ""
    ctx: int = 8192
    ngl: int = 99
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    api_key_ref: str = ""  # 密钥在 KeyStore 里挂哪个 id，留空就用模型 id
    # 单价，单位「元 / 百万 tokens」。本地模型留 0 表示不计费。
    price_in: float = 0.0
    price_out: float = 0.0
    price_cached_in: float = 0.0  # 命中缓存的输入价；<=0 表示按 price_in 算

    def detect_kind(self) -> str:
        if self.kind:
            return self.kind
        return "cloud_api" if self.base_url else "local_gguf"


@dataclass
class EngineConfig:
    name: str
    bin: str
    threads: int = 8
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ModeConfig:
    name: str
    model: str
    system_prompt: Path
    tools: list[str] = field(default_factory=list)
    require_confirm: bool = False
    max_tokens: int = 512
    temperature: float = 0.7
    # 档位级上下文预算：0 = 继承 [app].context_tokens（同样是 0 就按模型窗口自动）
    context_tokens: int = 0
    label: str = ""  # 显示名，例如「聊天」；留空就用内部 id

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass
class AppConfig:
    root: Path
    default_mode: str
    log_dir: Path
    data_dir: Path
    model_dirs: list[Path]
    models: dict[str, ModelConfig]
    modes: dict[str, ModeConfig]
    engines: dict[str, EngineConfig]
    web_backend: str = "auto"  # auto | deepseek | bing
    work_root: Path | None = None  # work 档文件工具的根目录，默认项目根
    context_tokens: int = 0  # prompt 预算；0 = 按模型窗口自动
    keep_recent_turns: int = 4  # 压缩上下文时至少保留最近几轮原文
    tool_output_chars: int = 6000  # 单个工具结果最多入库多少字符
    max_steps: int = 12  # 单条消息最多走几步（工具调用来回算一步）
    auto_confirm: bool = False  # 最高权限：所有工具（含高风险）都不再询问
    voice: "VoiceSettings" = field(default_factory=lambda: _default_voice())

    def model(self, model_id: str) -> ModelConfig:
        if model_id not in self.models:
            known = ", ".join(sorted(self.models)) or "（无）"
            raise ConfigError(f"未知模型 '{model_id}'，可选：{known}")
        return self.models[model_id]

    def mode(self, name: str) -> ModeConfig:
        if name not in self.modes:
            known = ", ".join(sorted(self.modes))
            raise ConfigError(f"未知档位 '{name}'，可选：{known}")
        return self.modes[name]

    def engine(self, name: str) -> EngineConfig:
        if name not in self.engines:
            known = ", ".join(sorted(self.engines)) or "（无）"
            raise ConfigError(f"未知引擎 '{name}'，已配置：{known}")
        return self.engines[name]

    def abs(self, value: str | Path) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (self.root / p)


# ---------- 解析 ----------


def _pick(dc_type, raw: dict, *, drop: tuple[str, ...] = ()) -> dict:
    all_fields = set(dc_type.__dataclass_fields__)
    unknown = set(raw) - all_fields
    if unknown:
        raise ConfigError(f"{dc_type.__name__} 里有未知字段：{', '.join(sorted(unknown))}")
    return {k: v for k, v in raw.items() if k in all_fields and k not in drop}


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


# ---------- 界面里改音色的持久化 ----------
# 不去改 config.toml（那里有注释和顺序，程序改写容易毁掉手工编辑），
# 而是在 .data/voice.json 里存一份覆盖值。.data/ 本来就不进版本库。


def _apply_voice_overlay(voice: VoiceSettings, data_dir: Path) -> VoiceSettings:
    path = Path(data_dir) / VOICE_OVERLAY
    if not path.exists():
        return voice
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return voice
    if not isinstance(raw, dict):
        return voice
    for key, value in raw.items():
        if hasattr(voice, key) and not key.startswith("_"):
            try:
                setattr(voice, key, value)
            except Exception:
                pass
    return voice


def save_voice_preference(data_dir: Path, **fields) -> Path:
    """存一条音色偏好。只接受 VoiceSettings 上真实存在的字段。"""
    path = Path(data_dir) / VOICE_OVERLAY
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    valid = set(VoiceSettings.__dataclass_fields__)
    for key, value in fields.items():
        if key in valid and value is not None:
            data[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_config(config_path: str | Path | None = None) -> AppConfig:
    root = Path(__file__).resolve().parents[1]
    path = Path(config_path) if config_path else root / "config.toml"
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}")

    # 用 utf-8-sig：记事本等编辑器可能写入 BOM，tomllib 会因此报 "Invalid statement"
    text = path.read_text(encoding="utf-8-sig")
    data = tomllib.loads(text)

    app = data.get("app", {})

    def resolve(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (root / p)

    log_dir = resolve(app.get("log_dir", "logs"))
    data_dir = resolve(app.get("data_dir", ".data"))
    model_dirs = [resolve(d) for d in _as_list(app.get("model_dirs", ["models"]))]

    engines: dict[str, EngineConfig] = {}
    for name, raw in data.get("engines", {}).items():
        engines[name] = EngineConfig(name=name, **_pick(EngineConfig, raw, drop=("name",)))

    models: dict[str, ModelConfig] = {}
    for mid, raw in data.get("models", {}).items():
        models[mid] = ModelConfig(id=mid, **_pick(ModelConfig, raw, drop=("id",)))

    if not models:
        raise ConfigError("配置里没有任何 [models.*]")

    modes: dict[str, ModeConfig] = {}
    for name, raw in data.get("modes", {}).items():
        raw = dict(raw)
        if "tools" in raw:
            raw["tools"] = _as_list(raw["tools"])
        prompt = Path(raw.get("system_prompt", f"prompts/{name}.md"))
        prompt_path = prompt if prompt.is_absolute() else (root / prompt)
        if not prompt_path.exists():
            raise ConfigError(f"档位 '{name}' 的 system_prompt 不存在：{prompt_path}")
        modes[name] = ModeConfig(
            name=name,
            system_prompt=prompt_path,
            **_pick(ModeConfig, raw, drop=("name", "system_prompt")),
        )

    if not modes:
        raise ConfigError("配置里没有任何 [modes.*]")

    for mode in modes.values():
        if mode.model not in models:
            raise ConfigError(f"档位 '{mode.name}' 指向不存在的模型 '{mode.model}'")

    default_mode = app.get("default_mode", next(iter(modes)))
    if default_mode not in modes:
        raise ConfigError(f"default_mode '{default_mode}' 不在档位列表里")

    voice_raw = data.get("voice", {})
    fallbacks_raw = voice_raw.get("asr_fallbacks")
    if fallbacks_raw is None:
        asr_fallbacks = tuple(resolve(item) for item in VoiceSettings.asr_fallbacks)
    elif isinstance(fallbacks_raw, str):
        asr_fallbacks = (str(resolve(fallbacks_raw)),)
    else:
        asr_fallbacks = tuple(str(resolve(item)) for item in fallbacks_raw)
    voice = VoiceSettings(
        asr_model=str(resolve(voice_raw.get("asr_model", VoiceSettings.asr_model))),
        asr_fallbacks=asr_fallbacks,
        kws_model=str(resolve(voice_raw.get("kws_model", VoiceSettings.kws_model))),
        wake_word=str(voice_raw.get("wake_word", VoiceSettings.wake_word)),
        silence_ms=int(voice_raw.get("silence_ms", VoiceSettings.silence_ms)),
        max_seconds=float(voice_raw.get("max_seconds", VoiceSettings.max_seconds)),
        kws_enabled=bool(voice_raw.get("kws_enabled", VoiceSettings.kws_enabled)),
        kws_threshold=float(
            voice_raw.get("kws_threshold", VoiceSettings.kws_threshold)
        ),
        vad_energy_floor=float(
            voice_raw.get("vad_energy_floor", VoiceSettings.vad_energy_floor)
        ),
        vad_noise_gain=float(
            voice_raw.get("vad_noise_gain", VoiceSettings.vad_noise_gain)
        ),
        vad_hold_ratio=float(
            voice_raw.get("vad_hold_ratio", VoiceSettings.vad_hold_ratio)
        ),
        vad_min_speech_ms=int(
            voice_raw.get("vad_min_speech_ms", VoiceSettings.vad_min_speech_ms)
        ),
        vad_engine=str(voice_raw.get("vad_engine", VoiceSettings.vad_engine)),
        silero_vad_model=str(
            resolve(voice_raw.get("silero_vad_model", VoiceSettings.silero_vad_model))
        ),
        tts_model=str(resolve(voice_raw.get("tts_model", VoiceSettings.tts_model))),
        tts_speed=float(voice_raw.get("tts_speed", VoiceSettings.tts_speed)),
        tts_speaker=int(voice_raw.get("tts_speaker", VoiceSettings.tts_speaker)),
        tts_enabled=bool(voice_raw.get("tts_enabled", VoiceSettings.tts_enabled)),
        tts_volume=float(voice_raw.get("tts_volume", VoiceSettings.tts_volume)),
        tts_noise_scale=float(
            voice_raw.get("tts_noise_scale", VoiceSettings.tts_noise_scale)
        ),
        tts_noise_scale_w=float(
            voice_raw.get("tts_noise_scale_w", VoiceSettings.tts_noise_scale_w)
        ),
        tts_fx=bool(voice_raw.get("tts_fx", VoiceSettings.tts_fx)),
        tts_fx_warmth=float(
            voice_raw.get("tts_fx_warmth", VoiceSettings.tts_fx_warmth)
        ),
        tts_fx_clarity=float(
            voice_raw.get("tts_fx_clarity", VoiceSettings.tts_fx_clarity)
        ),
        tts_fx_deharsh=float(
            voice_raw.get("tts_fx_deharsh", VoiceSettings.tts_fx_deharsh)
        ),
        tts_fx_space=float(voice_raw.get("tts_fx_space", VoiceSettings.tts_fx_space)),
        tts_fx_compression=float(
            voice_raw.get("tts_fx_compression", VoiceSettings.tts_fx_compression)
        ),
    )
    voice = _apply_voice_overlay(voice, data_dir)

    return AppConfig(
        root=root,
        default_mode=default_mode,
        log_dir=log_dir,
        data_dir=data_dir,
        model_dirs=model_dirs,
        models=models,
        modes=modes,
        engines=engines,
        web_backend=str(app.get("web_backend", "auto")),
        work_root=resolve(app.get("work_root", ".")),
        context_tokens=int(app.get("context_tokens", 0)),
        keep_recent_turns=int(app.get("keep_recent_turns", 4)),
        tool_output_chars=int(app.get("tool_output_chars", 6000)),
        max_steps=max(1, int(app.get("max_steps", 12))),
        auto_confirm=bool(app.get("auto_confirm", False)),
        voice=voice,
    )
