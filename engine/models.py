from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .battle import BattleEngine


class Buff:
    def __init__(self, name: str, value: float, duration_rounds: int, max_stacks: int = 1, is_debuff: bool = False):
        self.name = name
        self.value = value
        self.duration = duration_rounds
        self.stacks = 1
        self.max_stacks = max_stacks
        self.is_debuff = is_debuff


@dataclass
class Status:
    name: str
    duration: int
    stacks: int = 1
    tags: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    hooks: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    source_name: Optional[str] = None


class Effect:
    def __init__(self, type_: str, **params):
        self.type = type_
        self.params = params


class Skill:
    def __init__(self, name: str, effects: List[Effect]):
        self.name = name
        self.effects = effects


class Passive:
    def __init__(self, name: str, trigger_event: str, effects: List[Effect]):
        self.name = name
        self.trigger_event = trigger_event
        self.effects = effects


@dataclass
class EffectContext:
    battle: "BattleEngine"
    caster: "Hero"
    targets: List["Hero"] = field(default_factory=list)
    event: str = ""
    round: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    damage_dealt: float = 0.0
    status: Optional[Status] = None


class Hero:
    def __init__(self, name: str, speed: int, atk: float, hp: float, defense: float):
        self.name = name
        self.speed = speed
        self.atk = atk
        self.hp = hp
        self.max_hp = hp
        self.shield = 0.0
        self.max_shield = hp
        self.defense = defense
        self.crit_chance = 0.0
        self.crit_damage = 1.5
        self.energy = 0.0
        self.is_alive = True
        self.team = None

        self.buffs: List[Buff] = []
        self.passives: List[Passive] = []
        self.active_skill: Optional[Skill] = None

        self.stacks: Dict[str, int] = defaultdict(int)
        self.statuses: List[Status] = []
        self.behavior: Dict[str, Any] = {}
        self.modifiers: Dict[str, List[Callable[[float, "Hero", "Hero"], float]]] = defaultdict(list)

    def compute_final_atk(self) -> float:
        atk_bonus = sum(b.value for b in self.buffs if b.name == "atk_buff")
        return self.atk * (1 + atk_bonus)

    def compute_final_speed(self) -> int:
        speed_bonus = int(sum(b.value for b in self.buffs if b.name == "speed_buff"))
        status_speed = int(sum(s.data.get("speed_delta", 0) for s in self.statuses))
        return max(1, self.speed + speed_bonus + status_speed)

    def get_status(self, name: str) -> Optional[Status]:
        for status in self.statuses:
            if status.name == name:
                return status
        return None

    def has_status_tag(self, tag: str) -> bool:
        return any(tag in status.tags for status in self.statuses)


class Team:
    def __init__(self, heroes: List[Hero], number: int):
        self.heroes = heroes
        self.number = number
        self.opposite = None
        for hero in heroes:
            hero.team = self
