"""REPL 入口。

刻意做得很薄：参数解析 + 打印 + 斜杠命令，逻辑都在 Agent 里。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import Agent
from .config import ConfigError, load_config
from .provider import ProviderError

BANNER = """EVE v{version} — 本地优先 agent
档位：{modes}
/provider：{provider} → {model} ({base_url})
输入 /help 看命令，/exit 退出。
"""


def _print_meta(result_latency: int | None, usage: dict | None) -> None:
    if result_latency is None:
        return
    bits = [f"{result_latency / 1000:.1f}s"]
    if usage:
        bits.append(f"tok {usage.get('prompt_tokens', '?')}→{usage.get('completion_tokens', '?')}")
    print(f"  \033[90m[{', '.join(bits)}]\033[0m", flush=True)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="eve", description="本地优先的个人 agent")
    parser.add_argument("--mode", help="启动档位（默认读 config.toml 的 default_mode）")
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
        agent = Agent(cfg, mode_name)
    except (ConfigError, ProviderError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    def confirm(spec, tool_args) -> bool:
        if args.yes:
            return True
        preview = ", ".join(f"{k}={v}" for k, v in tool_args.items()) or "无参数"
        answer = input(f"  工具 {spec.name}({preview}) 需要确认，执行吗？[y/N] ").strip().lower()
        return answer in {"y", "yes"}

    def run_once(text: str) -> int:
        try:
            reply = agent.send(text, confirm=confirm)
        except (ProviderError, ConfigError) as exc:
            print(f"调用失败：{exc}", file=sys.stderr)
            return 1
        print(reply.strip())
        return 0

    if args.once:
        return run_once(args.once)

    p = agent.provider_cfg
    print(
        BANNER.format(
            version=__import__("eve").__version__,
            modes=" / ".join(sorted(cfg.modes)),
            provider=p.name,
            model=agent.provider.model,
            base_url=p.base_url,
        )
    )
    if agent.warning:
        print(f"注意：{agent.warning}\n")

    while True:
        try:
            line = input(f"\033[1mEVE[{agent.mode_name}]>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line == "/help":
            print(
                "  /mode <chat|desktop|work>  切换档位（会重置上下文）\n"
                "  /reset                     清空上下文\n"
                "  /tools                     当前档位可用工具\n"
                "  /where                     当前 provider / 模型\n"
                "  /exit                      退出"
            )
            continue
        if line == "/where":
            p = agent.provider_cfg
            print(f"  provider={p.name} model={agent.provider.model} url={p.base_url}")
            print(f"  mode={agent.mode_name} provider_enabled={p.enabled}")
            continue
        if line == "/tools":
            print(f"  可用：{', '.join(agent.active_tools) or '（无）'}")
            continue
        if line == "/reset":
            agent.reset()
            print("  上下文已清空")
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
        except (ProviderError, ConfigError) as exc:
            print(f"  调用失败：{exc}")
            continue
        except KeyboardInterrupt:
            print("\n  已中断")
            continue
        print(reply.strip())


if __name__ == "__main__":
    raise SystemExit(main())
