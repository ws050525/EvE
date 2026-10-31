"""REPL 入口。

刻意做得很薄：参数解析 + 打印 + 斜杠命令，逻辑都在 Agent / RuntimeManager 里。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from eve_agent import Agent
from eve_core.catalog import scan
from eve_core.config import ConfigError, load_config
from eve_core.provider import ProviderError
from eve_core.runtime import RuntimeManager
from eve_core.version import __version__

BANNER = """EVE v{version} — 本地优先 agent
档位：{modes}
当前：{mode} 档 / 模型 {model}（{kind}，{state}）
输入 /help 看命令，/exit 退出。
"""

HELP = """  /models              列出已配置的模型
  /use <id>            给当前档位换模型（保留对话上下文）
  /scan                扫描磁盘上可用的 GGUF
  /mode <name>         切换档位（会重置上下文）
  /status              运行时状态（引擎、端口、加载耗时）
  /persona             人格状态（抗议等级、连续命令数、颜文字节流）
  /stop                停止本地引擎，释放显存
  /key <id>            为云端模型保存 API Key（DPAPI 加密）
  /reset               清空上下文
  /tools               当前档位可用工具
  /where               当前 provider / 模型 / 端点
  /exit                退出"""


class StatusLine:
    """一行状态提示，用 \\r 原地刷新；非 TTY 时自动静默。"""

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() or os.environ.get("EVE_STATUS") == "1"
        self._width = 0

    def show(self, text: str) -> None:
        if not self.enabled:
            return
        line = f"  · {text}"
        pad = max(0, self._width - len(line))
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self._width = len(line)

    def clear(self) -> None:
        if not self.enabled or self._width == 0:
            return
        sys.stdout.write("\r" + " " * self._width + "\r")
        sys.stdout.flush()
        self._width = 0


def _kind_label(kind: str) -> str:
    return {"local_gguf": "本地", "cloud_api": "云端"}.get(kind, kind)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="eve", description="本地优先的个人 agent")
    parser.add_argument("--mode", help="启动档位（默认读 config.toml 的 default_mode）")
    parser.add_argument("--model", help="临时覆盖档位使用的模型 id")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--once", metavar="TEXT", help="非交互执行一句话后退出")
    parser.add_argument("--yes", action="store_true", help="非交互模式下自动批准工具调用")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    mode_name = args.mode or cfg.default_mode

    try:
        mode = cfg.mode(mode_name)
        model_id = args.model or mode.model
        model = cfg.model(model_id)
        kind = model.detect_kind()
        # 模型按需加载：这里只建对象，不发请求，所以 /models /scan 都是秒回
        agent = Agent(cfg, mode_name)
        runtime = agent.runtime
        if args.model:
            agent.switch_model(args.model)
    except (ConfigError, ProviderError, OSError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    status_line = StatusLine()

    def on_event(ev) -> None:
        data = ev.data
        if ev.kind == "engine_starting":
            status_line.show(f"加载模型 {data.get('label') or data.get('model_id')}…")
        elif ev.kind == "engine_ready":
            status_line.show(f"就绪（{data.get('load_seconds')}s）")
        elif ev.kind == "engine_stopped":
            status_line.show("引擎已停止")
        elif ev.kind == "thinking":
            status_line.show("思考中…")
        elif ev.kind == "tool_start":
            status_line.show(f"调用 {data.get('name')}")
        elif ev.kind == "tool_end":
            status_line.show(f"{data.get('name')} {'完成' if data.get('ok') else '失败'}")
        elif ev.kind == "error":
            status_line.clear()

    agent.bus.subscribe(on_event)

    def confirm(spec, tool_args) -> bool:
        if args.yes:
            return True
        preview = ", ".join(f"{k}={v}" for k, v in tool_args.items()) or "无参数"
        answer = input(f"  工具 {spec.name}({preview}) 需要确认，执行吗？[y/N] ").strip().lower()
        return answer in {"y", "yes"}

    if args.once:
        if kind == "local_gguf":
            print("（首次使用会拉起本地引擎，约 3–6 秒）", file=sys.stderr, flush=True)
        try:
            reply = agent.send(args.once, confirm=confirm)
        except (ProviderError, ConfigError) as exc:
            status_line.clear()
            print(f"调用失败：{exc}", file=sys.stderr)
            return 1
        finally:
            runtime.shutdown()
        status_line.clear()
        print(reply.strip())
        return 0

    status = runtime.status()
    model = cfg.model(agent.model_id)
    print(
        BANNER.format(
            version=__version__,
            modes=" / ".join(sorted(cfg.modes)),
            mode=agent.mode_name,
            model=f"{agent.model_id} · {model.label}",
            kind=_kind_label(model.detect_kind()),
            state=status.state,
        )
    )
    if agent.warning:
        print(f"注意：{agent.warning}\n")

    while True:
        try:
            line = input(f"\033[1mEVE[{agent.mode_name}/{agent.model_id}]>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            runtime.shutdown()
            return 0

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            runtime.shutdown()
            return 0
        if line == "/help":
            print(HELP)
            continue
        if line == "/models":
            active = agent.model_id
            for mid, m in sorted(cfg.models.items()):
                mark = "*" if mid == active else " "
                print(f"  {mark} {mid:<20} {_kind_label(m.detect_kind()):<4} {m.label}")
            print("  （* 为当前使用；用 /use <id> 切换）")
            continue
        if line == "/scan":
            found = scan(cfg.model_dirs)
            if not found:
                print("  没扫描到 GGUF。检查 config.toml 的 [app].model_dirs")
                continue
            for item in found:
                mm = f"，mmproj: {item.mmproj.name}" if item.mmproj else ""
                print(f"  {item.name}\n      {item.size_gb} GB  {item.quant}{mm}\n      {item.path}")
            print(f"  共 {len(found)} 个。要使用请在 config.toml 里加一个 [models.*] 段。")
            continue
        if line == "/persona":
            snap = agent.persona.snapshot()
            print("  " + "  ".join(f"{k}={v}" for k, v in snap.items()))
            continue
        if line == "/status":
            st = runtime.status()
            print(f"  model={st.model_id or '（未加载）'} kind={_kind_label(st.kind)} state={st.state}")
            if st.base_url:
                print(f"  base_url={st.base_url}")
            if st.pid:
                print(f"  pid={st.pid} 加载耗时={st.load_seconds}s")
            if st.log_path:
                print(f"  引擎日志={st.log_path}")
            if st.error:
                print(f"  错误={st.error}")
            continue
        if line == "/stop":
            runtime.shutdown()
            print("  本地引擎已停止")
            continue
        if line == "/where":
            p = agent.provider
            print(f"  model={agent.model_id} base_url={getattr(p.cfg, 'base_url', '?')}")
            continue
        if line == "/tools":
            print(f"  可用：{', '.join(agent.active_tools) or '（无）'}")
            continue
        if line == "/reset":
            agent.reset()
            print("  上下文已清空")
            continue
        if line.startswith("/key"):
            parts = line.split()
            if len(parts) != 2:
                print("  用法：/key <model_id>")
                continue
            mid = parts[1]
            if mid not in cfg.models:
                print(f"  没有模型 {mid}")
                continue
            try:
                secret = getpass.getpass("  粘贴 API Key（不回显）：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  已取消")
                continue
            if not secret:
                print("  空值，未保存")
                continue
            runtime.keys.set(mid, secret)
            print(f"  已加密保存到 {runtime.keys.path}")
            continue
        if line.startswith("/use"):
            parts = line.split()
            if len(parts) != 2:
                print(f"  用法：/use <{'|'.join(sorted(cfg.models))}>")
                continue
            try:
                print("  " + agent.switch_model(parts[1]))
            except (ConfigError, ProviderError) as exc:
                print(f"  切换失败：{exc}")
            continue
        if line.startswith("/mode"):
            parts = line.split()
            if len(parts) != 2:
                print(f"  用法：/mode <{'|'.join(sorted(cfg.modes))}>")
                continue
            try:
                print("  " + agent.switch_mode(parts[1]))
            except (ConfigError, ProviderError) as exc:
                print(f"  切换失败：{exc}")
            if agent.warning:
                print(f"  注意：{agent.warning}")
            continue
        if line.startswith("/"):
            print(f"  未知命令 {line}，试试 /help")
            continue

        try:
            reply = agent.send(line, confirm=confirm)
            status_line.clear()
            print(reply.strip())
        except (ProviderError, ConfigError) as exc:
            status_line.clear()
            print(f"  调用失败：{exc}")
        except KeyboardInterrupt:
            status_line.clear()
            print("\n  已中断")


if __name__ == "__main__":
    raise SystemExit(main())
