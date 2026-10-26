"""扫描磁盘上的 GGUF，配对视觉投影（mmproj），解析量化等级。

UI 的"选择本地模型"最终就调这里；CLI 的 `/scan` 也用它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_QUANT_RE = re.compile(
    r"(IQ\d+_[A-Z0-9_]+|Q\d+_K_[SML]|Q\d+_K|Q\d+_[01]|Q\d+|BF16|F16|F32)",
    re.IGNORECASE,
)


@dataclass
class DiscoveredModel:
    path: Path
    size_gb: float
    quant: str
    mmproj: Path | None = None

    @property
    def name(self) -> str:
        return self.path.stem


def _quant_of(filename: str) -> str:
    match = _QUANT_RE.search(filename)
    return match.group(1).upper() if match else "?"


def _is_mmproj(path: Path) -> bool:
    return "mmproj" in path.name.lower()


def _pair_mmproj(model: Path, candidates: list[Path]) -> Path | None:
    """同目录下优先取名字最相近的 mmproj；否则退回目录里唯一那个。"""
    same_dir = [p for p in candidates if p.parent == model.parent]
    if not same_dir:
        return None
    if len(same_dir) == 1:
        return same_dir[0]

    def score(candidate: Path) -> int:
        a = set(model.stem.lower().replace("-", " ").replace("_", " ").split())
        b = set(candidate.stem.lower().replace("-", " ").replace("_", " ").split())
        return len(a & b)

    ranked = sorted(same_dir, key=score, reverse=True)
    return ranked[0] if ranked else None


def scan(model_dirs: list[Path], *, with_mmproj: bool = False) -> list[DiscoveredModel]:
    """扫描目录，返回可选的主模型列表。"""
    files: list[Path] = []
    for directory in model_dirs:
        if not directory.exists():
            continue
        files.extend(p for p in directory.rglob("*.gguf") if p.is_file())

    projections = [p for p in files if _is_mmproj(p)]
    mains = [p for p in files if not _is_mmproj(p)]

    result: list[DiscoveredModel] = []
    for path in sorted(mains, key=lambda p: p.stat().st_size, reverse=True):
        try:
            size_gb = path.stat().st_size / 1024 ** 3
        except OSError:
            continue
        result.append(
            DiscoveredModel(
                path=path,
                size_gb=round(size_gb, 2),
                quant=_quant_of(path.name),
                mmproj=_pair_mmproj(path, projections),
            )
        )
    return result
