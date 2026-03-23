from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Any

import uvicorn

from .logging import JsonlLogger, ensure_dir
from .config import DEFAULT_CONFIG_PATH, load_config, render_client_env, save_config
from .proxy import CaptureProxy, ProxyStage, parse_listen


def _add_common_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to profiles JSON")


def _read_config(path: str) -> Any:
    return load_config(path)


def cmd_serve(args: argparse.Namespace) -> int:
    stage = ProxyStage(name=args.stage, listen=args.listen, upstream=args.upstream)
    logger_root = ensure_dir(args.log_dir)
    proxy = CaptureProxy(stage=stage, logger=JsonlLogger(logger_root))
    host, port = parse_listen(args.listen)
    uvicorn.run(proxy.app, host=host, port=port, log_level=args.log_level, access_log=False)
    return 0


def _profile_stages(config, profile_name: str):
    if profile_name not in config.profiles:
        raise KeyError(f"unknown profile: {profile_name}")
    return config.profiles[profile_name].stages


def cmd_show(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    profile_name = args.profile or config.active_profile
    if not profile_name:
        raise SystemExit("no active profile set; pass --profile")
    profile = config.profiles[profile_name]
    print(f"profile: {profile.name}")
    print(f"client: {profile.client}")
    print(f"entry_url: {profile.entry_url}")
    for idx, stage in enumerate(profile.stages, 1):
        print(f"stage {idx}: {stage.name} listen={stage.listen} upstream={stage.upstream}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    profile_name = args.profile or config.active_profile
    if not profile_name:
        raise SystemExit("no active profile set; pass --profile")
    profile = config.profiles[profile_name]
    client_name = args.client or profile.client
    env = render_client_env(config, client_name, profile_name)
    for key, value in env.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    profile_name = args.profile or config.active_profile
    if not profile_name:
        raise SystemExit("no active profile set; pass --profile")
    profile = config.profiles[profile_name]
    client_name = args.client or profile.client
    env = os.environ.copy()
    env.update(render_client_env(config, client_name, profile_name))
    command = args.cmd
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("exec requires a command after --")
    completed = subprocess.run(command, env=env)
    return completed.returncode


def cmd_switch(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    if args.profile not in config.profiles:
        raise SystemExit(f"unknown profile: {args.profile}")
    config.active_profile = args.profile
    save_config(args.config, config)
    print(args.profile)
    return 0


def _launch_stage(stage, log_dir: str):
    cmd = [
        sys.executable,
        "-m",
        "tapchain",
        "serve",
        "--stage",
        stage.name,
        "--listen",
        stage.listen,
        "--upstream",
        stage.upstream,
        "--log-dir",
        log_dir,
    ]
    return subprocess.Popen(cmd)


def cmd_run(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    profile_name = args.profile or config.active_profile
    if not profile_name:
        raise SystemExit("no active profile set; pass --profile")
    stages = _profile_stages(config, profile_name)
    if not stages:
        raise SystemExit(f"profile {profile_name} has no stages")

    processes: list[subprocess.Popen] = []

    def stop_processes() -> None:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 5.0
        while time.time() < deadline and any(proc.poll() is None for proc in processes):
            time.sleep(0.1)
        for proc in processes:
            if proc.poll() is None:
                proc.kill()

    try:
        for stage in reversed(stages):
            processes.append(_launch_stage(stage, args.log_dir))
        print(f"started {len(processes)} stages for profile {profile_name}")
        print(f"logs: {args.log_dir}")

        def terminate(*_):
            stop_processes()

        signal.signal(signal.SIGINT, terminate)
        signal.signal(signal.SIGTERM, terminate)

        while True:
            exit_codes: list[int] = []
            for proc in processes:
                code = proc.poll()
                if code is not None:
                    exit_codes.append(code)
            if exit_codes:
                stop_processes()
                for code in exit_codes:
                    if code != 0:
                        return code
                return 0
            time.sleep(0.5)
    finally:
        stop_processes()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tapchain")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run one capture proxy stage")
    serve.add_argument("--stage", required=True, help="Stage name for logs")
    serve.add_argument("--listen", required=True, help="Host:port to bind")
    serve.add_argument("--upstream", required=True, help="Upstream URL")
    serve.add_argument("--log-dir", default="logs", help="Log directory")
    serve.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug", "trace"])
    serve.set_defaults(func=cmd_serve)

    show = sub.add_parser("show", help="Show the active profile")
    _add_common_config_args(show)
    show.add_argument("--profile", help="Profile name")
    show.set_defaults(func=cmd_show)

    env = sub.add_parser("env", help="Print client environment exports")
    _add_common_config_args(env)
    env.add_argument("--profile", help="Profile name")
    env.add_argument("--client", help="Client name")
    env.set_defaults(func=cmd_env)

    exec_cmd = sub.add_parser("exec", help="Run a client command with profile env")
    _add_common_config_args(exec_cmd)
    exec_cmd.add_argument("--profile", help="Profile name")
    exec_cmd.add_argument("--client", help="Client name")
    exec_cmd.add_argument("cmd", nargs=argparse.REMAINDER, help="Command after --")
    exec_cmd.set_defaults(func=cmd_exec)

    switch = sub.add_parser("switch", help="Set the active profile in config")
    _add_common_config_args(switch)
    switch.add_argument("profile", help="Profile name")
    switch.set_defaults(func=cmd_switch)

    run = sub.add_parser("run", help="Start all stages in a profile")
    _add_common_config_args(run)
    run.add_argument("--profile", help="Profile name")
    run.add_argument("--log-dir", default="logs", help="Log directory")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
