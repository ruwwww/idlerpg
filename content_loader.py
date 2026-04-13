from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Protocol


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
class HeroDef:
    id: str
    name: str
    speed: int
    atk: float
    hp: float
    defense: float
    active_skill_id: str | None
    passive_ids: List[str]


class HeroContentSource(Protocol):
    def get_hero(self, hero_id: str) -> HeroDef:
        ...

    def get_skill(self, skill_id: str) -> SkillDef:
        ...

    def get_passive(self, passive_id: str) -> PassiveDef:
        ...

    def get_team_hero_ids(self, team_id: str) -> List[str]:
        ...

    def validate_references(self) -> None:
        ...


class JsonHeroContentSource:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._raw: Dict[str, Any] = self._read_json()

        self._skills: Dict[str, SkillDef] = {
            skill_id: self._parse_skill(skill_id, data)
            for skill_id, data in self._raw.get("skills", {}).items()
        }
        self._passives: Dict[str, PassiveDef] = {
            passive_id: self._parse_passive(passive_id, data)
            for passive_id, data in self._raw.get("passives", {}).items()
        }
        self._heroes: Dict[str, HeroDef] = {
            hero_id: self._parse_hero(hero_id, data)
            for hero_id, data in self._raw.get("heroes", {}).items()
        }
        self._teams: Dict[str, List[str]] = {
            team_id: list(hero_ids)
            for team_id, hero_ids in self._raw.get("teams", {}).items()
        }

        self.validate_references()

    def _read_json(self) -> Dict[str, Any]:
        with self.file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

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

    def _parse_hero(self, hero_id: str, hero_data: Dict[str, Any]) -> HeroDef:
        return HeroDef(
            id=hero_id,
            name=hero_data["name"],
            speed=int(hero_data["speed"]),
            atk=float(hero_data["atk"]),
            hp=float(hero_data["hp"]),
            defense=float(hero_data["defense"]),
            active_skill_id=hero_data.get("active_skill"),
            passive_ids=list(hero_data.get("passives", [])),
        )

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

    def get_team_hero_ids(self, team_id: str) -> List[str]:
        try:
            return self._teams[team_id]
        except KeyError as exc:
            raise KeyError(f"Unknown team id '{team_id}'") from exc

    def validate_references(self) -> None:
        for hero in self._heroes.values():
            if hero.active_skill_id and hero.active_skill_id not in self._skills:
                raise ValueError(
                    f"Hero '{hero.id}' references unknown skill '{hero.active_skill_id}'"
                )
            for passive_id in hero.passive_ids:
                if passive_id not in self._passives:
                    raise ValueError(
                        f"Hero '{hero.id}' references unknown passive '{passive_id}'"
                    )

        for team_id, hero_ids in self._teams.items():
            for hero_id in hero_ids:
                if hero_id not in self._heroes:
                    raise ValueError(
                        f"Team '{team_id}' references unknown hero '{hero_id}'"
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
