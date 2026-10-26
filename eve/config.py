"""配置加载。

v0.4 起，"模型"是一等公民：
- `[models.*]` 声明有哪些模型（本地 GGUF 或云端 API）
- `[modes.*]` 只声明"用哪个 model_id"，不再区分 local / cloud
- `[engines.*]` 声明推理引擎本体（可执行文件、线程数、额外参数）

这样换本地模型、换云端厂商、甚至给同一个档位换成本地模型，都只是改数据。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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

    return AppConfig(
        root=root,
        default_mode=default_mode,
        log_dir=log_dir,
        data_dir=data_dir,
        model_dirs=model_dirs,
        models=models,
        modes=modes,
        engines=engines,
    )
