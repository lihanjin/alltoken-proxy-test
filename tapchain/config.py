from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


DEFAULT_CONFIG_PATH = Path("profiles.example.json")


@dataclass
class ClientTemplate:
    name: str
    env: dict[str, str]
    api_key: str = "sk-local"


@dataclass
class StageConfig:
    name: str
    listen: str
    upstream: str


@dataclass
class ProfileConfig:
    name: str
    client: str
    entry_url: str
    stages: list[StageConfig]


@dataclass
class TapConfig:
    active_profile: str | None
    clients: dict[str, ClientTemplate]
    profiles: dict[str, ProfileConfig]
    raw: dict[str, Any]


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_config(path: str | Path) -> TapConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    raw = _require_mapping(data, "config")

    clients: dict[str, ClientTemplate] = {}
    for client_name, client_data in _require_mapping(raw.get("clients", {}), "clients").items():
        client_map = _require_mapping(client_data, f"clients.{client_name}")
        env = _require_mapping(client_map.get("env", {}), f"clients.{client_name}.env")
        clients[client_name] = ClientTemplate(
            name=client_name,
            env={str(k): str(v) for k, v in env.items()},
            api_key=str(client_map.get("api_key", "sk-local")),
        )

    profiles: dict[str, ProfileConfig] = {}
    for profile_name, profile_data in _require_mapping(raw.get("profiles", {}), "profiles").items():
        profile_map = _require_mapping(profile_data, f"profiles.{profile_name}")
        stages_raw = profile_map.get("stages", [])
        if not isinstance(stages_raw, list):
            raise ValueError(f"profiles.{profile_name}.stages must be a list")
        stages: list[StageConfig] = []
        for stage_data in stages_raw:
            stage_map = _require_mapping(stage_data, f"profiles.{profile_name}.stages[]")
            stages.append(
                StageConfig(
                    name=str(stage_map["name"]),
                    listen=str(stage_map["listen"]),
                    upstream=str(stage_map["upstream"]),
                )
            )
        profiles[profile_name] = ProfileConfig(
            name=profile_name,
            client=str(profile_map["client"]),
            entry_url=str(profile_map["entry_url"]),
            stages=stages,
        )

    return TapConfig(
        active_profile=str(raw["active_profile"]) if raw.get("active_profile") else None,
        clients=clients,
        profiles=profiles,
        raw=raw,
    )


def save_config(path: str | Path, config: TapConfig) -> None:
    data = dict(config.raw)
    data["active_profile"] = config.active_profile
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_client_env(config: TapConfig, client_name: str, profile_name: str) -> dict[str, str]:
    if profile_name not in config.profiles:
        raise KeyError(f"unknown profile: {profile_name}")
    if client_name not in config.clients:
        raise KeyError(f"unknown client: {client_name}")

    profile = config.profiles[profile_name]
    client = config.clients[client_name]
    env: dict[str, str] = {}
    for key, template in client.env.items():
        env[key] = (
            template.replace("{entry_url}", profile.entry_url)
            .replace("{api_key}", client.api_key)
            .replace("{profile}", profile.name)
            .replace("{client}", client.name)
        )
    return env

