"""配置加载：一张 TOML 表就是整个模型层的接线板。

设计要点：
- 业务代码只认 `ProviderConfig` / `ModeConfig`，不认任何厂商。
- 密钥永远走环境变量（`api_key_env`），配置文件里不出现明文。
- 相对路径一律相对项目根目录解析，便于在不同机器上迁移。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """配置有问题时抛出，消息面向用户。"""


@dataclass
class ProviderConfig:
    name: str
    type: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    timeout: float = 180.0
    enabled: bool = True

    def resolved_key(self) -> str:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env, "").strip()
            if not value:
                raise ConfigError(
                    f"provider '{self.name}' 需要环境变量 {self.api_key_env}，当前为空"
                )
            return value
        return self.api_key or "not-needed"


@dataclass
class ModeConfig:
    name: str
    provider: str
    system_prompt: Path
    tools: list[str] = field(default_factory=list)
    require_confirm: bool = False
    max_tokens: int = 512
    temperature: float = 0.7


@dataclass
class EngineConfig:
    bin: str
    model: str
    mmproj: str = ""
    port: int = 18080
    ctx: int = 8192
    ngl: int = 99
    threads: int = 8
    extra_args: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    root: Path
    default_mode: str
    log_dir: Path
    providers: dict[str, ProviderConfig]
    modes: dict[str, ModeConfig]
    engine: EngineConfig | None = None

    def mode(self, name: str) -> ModeConfig:
        if name not in self.modes:
            known = ", ".join(sorted(self.modes))
            raise ConfigError(f"未知档位 '{name}'，可选：{known}")
        return self.modes[name]

    def provider_for(self, mode_name: str) -> ProviderConfig:
        mode = self.mode(mode_name)
        if mode.provider not in self.providers:
            raise ConfigError(
                f"档位 '{mode_name}' 指向不存在的 provider '{mode.provider}'"
            )
        provider = self.providers[mode.provider]
        if not provider.enabled:
            raise ConfigError(
                f"provider '{provider.name}' 未启用（enabled = false）。"
                f"填好密钥后把 enabled 改成 true 即可。"
            )
        return provider


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

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    app = data.get("app", {})
    log_dir = Path(app.get("log_dir", "logs"))
    if not log_dir.is_absolute():
        log_dir = root / log_dir

    providers: dict[str, ProviderConfig] = {}
    for name, raw in data.get("providers", {}).items():
        providers[name] = ProviderConfig(name=name, **_pick(ProviderConfig, raw, drop=("name",)))

    modes: dict[str, ModeConfig] = {}
    for name, raw in data.get("modes", {}).items():
        raw = dict(raw)
        if "tools" in raw:
            raw["tools"] = _as_list(raw["tools"])
        prompt = raw.get("system_prompt", f"prompts/{name}.md")
        prompt_path = Path(prompt)
        if not prompt_path.is_absolute():
            prompt_path = root / prompt_path
        if not prompt_path.exists():
            raise ConfigError(f"档位 '{name}' 的 system_prompt 不存在：{prompt_path}")
        modes[name] = ModeConfig(
            name=name,
            provider=raw.get("provider", "local"),
            system_prompt=prompt_path,
            **_pick(ModeConfig, raw, drop=("name", "provider", "system_prompt")),
        )

    if not modes:
        raise ConfigError("配置里没有任何 [modes.*] 档位")

    engine_raw = data.get("engine")
    engine = EngineConfig(**_pick(EngineConfig, engine_raw)) if engine_raw else None

    default_mode = app.get("default_mode", next(iter(modes)))
    if default_mode not in modes:
        raise ConfigError(f"default_mode '{default_mode}' 不在档位列表里")

    return AppConfig(
        root=root,
        default_mode=default_mode,
        log_dir=log_dir,
        providers=providers,
        modes=modes,
        engine=engine,
    )
