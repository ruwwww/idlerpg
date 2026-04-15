from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class EffectDef:
    type: str
    params: Dict[str, Any]


@dataclass(frozen=True)
class SkillDef:
    id: str
    name: str
    effects: List[EffectDef]


@dataclass(frozen=True)
class PassiveDef:
    id: str
    name: str
    trigger_event: str
    effects: List[EffectDef]


@dataclass(frozen=True)
class ArtifactPassiveDef:
    name: str
    trigger_event: str
    effects: List[EffectDef]
    unique_key: str | None


@dataclass(frozen=True)
class ArtifactDef:
    id: str
    name: str
    stat_bonuses: Dict[str, float]
    passive: ArtifactPassiveDef | None


@dataclass(frozen=True)
class HeroDef:
    id: str
    name: str
    speed: int
    atk: float
    hp: float
    defense: float
    level: Optional[int]    # None = not set in JSON, factory applies default
    precision: float
    block: float
    basic_skill_id: str | None
    active_skill_id: str | None
    passive_ids: List[str]
    artifact_ids: List[str]


@dataclass(frozen=True)
class TeamMemberDef:
    hero_id: str
    artifact_ids: List[str] | None = None


class HeroContentSource(Protocol):
    def get_hero(self, hero_id: str) -> HeroDef:
        ...

    def get_skill(self, skill_id: str) -> SkillDef:
        ...

    def get_passive(self, passive_id: str) -> PassiveDef:
        ...

    def get_artifact(self, artifact_id: str) -> ArtifactDef:
        ...

    def get_team_hero_ids(self, team_id: str) -> List[str]:
        ...

    def get_team_members(self, team_id: str) -> List[TeamMemberDef]:
        ...

    def validate_references(self) -> None:
        ...


class JsonHeroContentSource:
    def __init__(self, source_path: str):
        self.source_path = Path(source_path)
        self._raw: Dict[str, Any] = self._read_source()
        self._validate_schema()

        self._skills: Dict[str, SkillDef] = {
            skill_id: self._parse_skill(skill_id, data)
            for skill_id, data in self._raw.get("skills", {}).items()
        }
        self._passives: Dict[str, PassiveDef] = {
            passive_id: self._parse_passive(passive_id, data)
            for passive_id, data in self._raw.get("passives", {}).items()
        }
        self._artifacts: Dict[str, ArtifactDef] = {
            artifact_id: self._parse_artifact(artifact_id, data)
            for artifact_id, data in self._raw.get("artifacts", {}).items()
        }
        self._heroes: Dict[str, HeroDef] = {
            hero_id: self._parse_hero(hero_id, data)
            for hero_id, data in self._raw.get("heroes", {}).items()
        }
        self._teams: Dict[str, List[TeamMemberDef]] = {
            team_id: [self._parse_team_member(entry) for entry in members]
            for team_id, members in self._raw.get("teams", {}).items()
        }

        self.validate_references()

    def _read_source(self) -> Dict[str, Any]:
        if self.source_path.is_file():
            with self.source_path.open("r", encoding="utf-8") as f:
                return json.load(f)

        if not self.source_path.is_dir():
            raise ValueError(f"Content source path does not exist: {self.source_path}")

        merged: Dict[str, Any] = {
            "skills": {},
            "passives": {},
            "artifacts": {},
            "heroes": {},
            "teams": {},
        }

        files = sorted(self.source_path.rglob("*.json"))
        if not files:
            raise ValueError(f"No JSON files found in content directory: {self.source_path}")

        for file_path in files:
            with file_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)

            if not isinstance(raw, dict):
                raise ValueError(f"Top-level JSON must be an object in {file_path}")

            for section in ["skills", "passives", "artifacts", "heroes", "teams"]:
                section_data = raw.get(section)
                if section_data is None:
                    continue
                if not isinstance(section_data, dict):
                    raise ValueError(f"Section '{section}' must be an object in {file_path}")

                for item_id, item_value in section_data.items():
                    if item_id in merged[section]:
                        raise ValueError(
                            f"Duplicate id '{item_id}' found in section '{section}' while loading {file_path}"
                        )
                    merged[section][item_id] = item_value

        return merged

    def _validate_schema(self) -> None:
        for skill_id, skill_data in self._raw.get("skills", {}).items():
            if not isinstance(skill_data, dict):
                raise ValueError(f"Skill '{skill_id}' must be an object")
            if "name" not in skill_data or not isinstance(skill_data["name"], str):
                raise ValueError(f"Skill '{skill_id}' is missing string field 'name'")
            effects = skill_data.get("effects", [])
            if not isinstance(effects, list):
                raise ValueError(f"Skill '{skill_id}' field 'effects' must be a list")

        for passive_id, passive_data in self._raw.get("passives", {}).items():
            if not isinstance(passive_data, dict):
                raise ValueError(f"Passive '{passive_id}' must be an object")
            if "name" not in passive_data or not isinstance(passive_data["name"], str):
                raise ValueError(f"Passive '{passive_id}' is missing string field 'name'")
            if "trigger_event" not in passive_data or not isinstance(passive_data["trigger_event"], str):
                raise ValueError(f"Passive '{passive_id}' is missing string field 'trigger_event'")
            effects = passive_data.get("effects", [])
            if not isinstance(effects, list):
                raise ValueError(f"Passive '{passive_id}' field 'effects' must be a list")

        for artifact_id, artifact_data in self._raw.get("artifacts", {}).items():
            if not isinstance(artifact_data, dict):
                raise ValueError(f"Artifact '{artifact_id}' must be an object")
            if "name" not in artifact_data or not isinstance(artifact_data["name"], str):
                raise ValueError(f"Artifact '{artifact_id}' is missing string field 'name'")
            bonuses = artifact_data.get("stat_bonuses", {})
            if not isinstance(bonuses, dict):
                raise ValueError(f"Artifact '{artifact_id}' field 'stat_bonuses' must be an object")
            passive_data = artifact_data.get("passive")
            if passive_data is not None:
                if not isinstance(passive_data, dict):
                    raise ValueError(f"Artifact '{artifact_id}' field 'passive' must be an object")
                if "trigger_event" not in passive_data or not isinstance(passive_data["trigger_event"], str):
                    raise ValueError(f"Artifact '{artifact_id}' passive is missing string field 'trigger_event'")
                if "effects" in passive_data and not isinstance(passive_data.get("effects", []), list):
                    raise ValueError(f"Artifact '{artifact_id}' passive field 'effects' must be a list")
                if "unique_key" in passive_data and not isinstance(passive_data.get("unique_key"), str):
                    raise ValueError(f"Artifact '{artifact_id}' passive field 'unique_key' must be a string")

        for hero_id, hero_data in self._raw.get("heroes", {}).items():
            if not isinstance(hero_data, dict):
                raise ValueError(f"Hero '{hero_id}' must be an object")
            required_fields = ["name", "speed", "atk", "hp", "defense"]
            for field in required_fields:
                if field not in hero_data:
                    raise ValueError(f"Hero '{hero_id}' is missing required field '{field}'")
            artifact_ids = hero_data.get("artifacts", [])
            if not isinstance(artifact_ids, list) or not all(isinstance(artifact_id, str) for artifact_id in artifact_ids):
                raise ValueError(f"Hero '{hero_id}' field 'artifacts' must be a list of string artifact ids")
            if len(artifact_ids) > 3:
                raise ValueError(f"Hero '{hero_id}' has {len(artifact_ids)} artifacts, maximum is 3")

        for team_id, hero_ids in self._raw.get("teams", {}).items():
            if not isinstance(hero_ids, list):
                raise ValueError(f"Team '{team_id}' must be a list of hero ids or hero objects")
            for member in hero_ids:
                if isinstance(member, str):
                    continue
                if not isinstance(member, dict):
                    raise ValueError(
                        f"Team '{team_id}' members must be strings or objects with hero_id/hero"
                    )
                hero_ref = member.get("hero_id", member.get("hero", member.get("id")))
                if not isinstance(hero_ref, str) or not hero_ref:
                    raise ValueError(
                        f"Team '{team_id}' object member must include string hero_id/hero/id"
                    )
                if "artifacts" in member:
                    artifact_ids = member.get("artifacts")
                    if not isinstance(artifact_ids, list) or not all(isinstance(a, str) for a in artifact_ids):
                        raise ValueError(
                            f"Team '{team_id}' member '{hero_ref}' field 'artifacts' must be a list of strings"
                        )
                    if len(artifact_ids) > 3:
                        raise ValueError(
                            f"Team '{team_id}' member '{hero_ref}' has {len(artifact_ids)} artifacts, maximum is 3"
                        )

    def _parse_effect(self, effect_data: Dict[str, Any]) -> EffectDef:
        if "type" not in effect_data:
            raise ValueError(f"Effect is missing required key 'type': {effect_data}")
        params = {k: v for k, v in effect_data.items() if k != "type"}
        return EffectDef(type=effect_data["type"], params=params)

    def _parse_skill(self, skill_id: str, skill_data: Dict[str, Any]) -> SkillDef:
        return SkillDef(
            id=skill_id,
            name=skill_data["name"],
            effects=[self._parse_effect(e) for e in skill_data.get("effects", [])],
        )

    def _parse_passive(self, passive_id: str, passive_data: Dict[str, Any]) -> PassiveDef:
        return PassiveDef(
            id=passive_id,
            name=passive_data["name"],
            trigger_event=passive_data["trigger_event"],
            effects=[self._parse_effect(e) for e in passive_data.get("effects", [])],
        )

    def _parse_artifact(self, artifact_id: str, artifact_data: Dict[str, Any]) -> ArtifactDef:
        stat_bonuses: Dict[str, float] = {}
        for key, value in artifact_data.get("stat_bonuses", {}).items():
            if not isinstance(value, (int, float)):
                raise ValueError(f"Artifact '{artifact_id}' stat bonus '{key}' must be numeric")
            stat_bonuses[key] = float(value)

        passive_data = artifact_data.get("passive")
        passive_def: ArtifactPassiveDef | None = None
        if isinstance(passive_data, dict):
            passive_name = passive_data.get("name")
            if not isinstance(passive_name, str):
                passive_name = f"{artifact_data['name']} Passive"
            passive_def = ArtifactPassiveDef(
                name=passive_name,
                trigger_event=passive_data["trigger_event"],
                effects=[self._parse_effect(e) for e in passive_data.get("effects", [])],
                unique_key=passive_data.get("unique_key") if isinstance(passive_data.get("unique_key"), str) else None,
            )

        return ArtifactDef(
            id=artifact_id,
            name=artifact_data["name"],
            stat_bonuses=stat_bonuses,
            passive=passive_def,
        )

    def _parse_hero(self, hero_id: str, hero_data: Dict[str, Any]) -> HeroDef:
        return HeroDef(
            id=hero_id,
            name=hero_data["name"],
            speed=int(hero_data["speed"]),
            atk=float(hero_data["atk"]),
            hp=float(hero_data["hp"]),
            defense=float(hero_data["defense"]),
            level=int(hero_data["level"]) if "level" in hero_data else None,
            precision=float(hero_data.get("precision", 0.0)),
            block=float(hero_data.get("block", 0.0)),
            basic_skill_id=hero_data.get("basic_attack"),
            active_skill_id=hero_data.get("active_skill"),
            passive_ids=list(hero_data.get("passives", [])),
            artifact_ids=list(hero_data.get("artifacts", [])),
        )

    def _parse_team_member(self, member_data: Any) -> TeamMemberDef:
        if isinstance(member_data, str):
            return TeamMemberDef(hero_id=member_data, artifact_ids=None)
        if not isinstance(member_data, dict):
            raise ValueError("Team member must be string hero id or object")

        hero_ref = member_data.get("hero_id", member_data.get("hero", member_data.get("id")))
        if not isinstance(hero_ref, str) or not hero_ref:
            raise ValueError("Team member object must include string hero_id/hero/id")

        artifact_ids: List[str] | None = None
        if "artifacts" in member_data:
            raw_artifacts = member_data.get("artifacts", [])
            artifact_ids = list(raw_artifacts)

        return TeamMemberDef(hero_id=hero_ref, artifact_ids=artifact_ids)

    def get_hero(self, hero_id: str) -> HeroDef:
        try:
            return self._heroes[hero_id]
        except KeyError as exc:
            raise KeyError(f"Unknown hero id '{hero_id}'") from exc

    def get_skill(self, skill_id: str) -> SkillDef:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise KeyError(f"Unknown skill id '{skill_id}'") from exc

    def get_passive(self, passive_id: str) -> PassiveDef:
        try:
            return self._passives[passive_id]
        except KeyError as exc:
            raise KeyError(f"Unknown passive id '{passive_id}'") from exc

    def get_artifact(self, artifact_id: str) -> ArtifactDef:
        try:
            return self._artifacts[artifact_id]
        except KeyError as exc:
            raise KeyError(f"Unknown artifact id '{artifact_id}'") from exc

    def get_team_hero_ids(self, team_id: str) -> List[str]:
        try:
            return [member.hero_id for member in self._teams[team_id]]
        except KeyError as exc:
            raise KeyError(f"Unknown team id '{team_id}'") from exc

    def get_team_members(self, team_id: str) -> List[TeamMemberDef]:
        try:
            return list(self._teams[team_id])
        except KeyError as exc:
            raise KeyError(f"Unknown team id '{team_id}'") from exc

    def validate_references(self) -> None:
        for hero in self._heroes.values():
            if hero.basic_skill_id and hero.basic_skill_id not in self._skills:
                raise ValueError(
                    f"Hero '{hero.id}' references unknown skill '{hero.basic_skill_id}'"
                )
            if hero.active_skill_id and hero.active_skill_id not in self._skills:
                raise ValueError(
                    f"Hero '{hero.id}' references unknown skill '{hero.active_skill_id}'"
                )
            for passive_id in hero.passive_ids:
                if passive_id not in self._passives:
                    raise ValueError(
                        f"Hero '{hero.id}' references unknown passive '{passive_id}'"
                    )
            for artifact_id in hero.artifact_ids:
                if artifact_id not in self._artifacts:
                    raise ValueError(
                        f"Hero '{hero.id}' references unknown artifact '{artifact_id}'"
                    )

        for team_id, members in self._teams.items():
            for member in members:
                if member.hero_id not in self._heroes:
                    raise ValueError(
                        f"Team '{team_id}' references unknown hero '{member.hero_id}'"
                    )
                if member.artifact_ids is not None:
                    for artifact_id in member.artifact_ids:
                        if artifact_id not in self._artifacts:
                            raise ValueError(
                                f"Team '{team_id}' member '{member.hero_id}' references unknown artifact '{artifact_id}'"
                            )

    def validate_effect_types(self, supported_effect_types: set[str]) -> None:
        for skill in self._skills.values():
            for effect in skill.effects:
                if effect.type not in supported_effect_types:
                    raise ValueError(
                        f"Skill '{skill.id}' uses unsupported effect type '{effect.type}'"
                    )
        for passive in self._passives.values():
            for effect in passive.effects:
                if effect.type not in supported_effect_types:
                    raise ValueError(
                        f"Passive '{passive.id}' uses unsupported effect type '{effect.type}'"
                    )
        for artifact in self._artifacts.values():
            if not artifact.passive:
                continue
            for effect in artifact.passive.effects:
                if effect.type not in supported_effect_types:
                    raise ValueError(
                        f"Artifact '{artifact.id}' passive uses unsupported effect type '{effect.type}'"
                    )
