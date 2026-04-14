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
    source_skill: Optional[str] = None


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
    damage_dealt_actual: float = 0.0
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
        self.energy = 50.0
        self.is_alive = True
        self.team = None

        self.combat_stats: Dict[str, float] = {
            "damage_dealt_hp": 0.0,
            "damage_dealt_shield": 0.0,
            "damage_taken_hp": 0.0,
            "damage_taken_shield": 0.0,
            "healing_done": 0.0,
            "shielding_done": 0.0
        }

        self.buffs: List[Buff] = []
        self.passives: List[Passive] = []
        self.basic_skill: Optional[Skill] = None
        self.active_skill: Optional[Skill] = None

        self.stacks: Dict[str, int] = defaultdict(int)
        self.stack_ttls: Dict[str, List[int]] = defaultdict(list)
        self.statuses: List[Status] = []
        self.behavior: Dict[str, Any] = {}
        self.modifiers: Dict[str, List[Callable[[float, "Hero", "Hero"], float]]] = defaultdict(list)

    def add_timed_stack(self, stack_name: str, amount: int, ttl_rounds: int) -> None:
        if amount <= 0 or ttl_rounds <= 0:
            return
        for _ in range(amount):
            self.stack_ttls[stack_name].append(int(ttl_rounds))
        self.stacks[stack_name] += amount

    def consume_timed_stack(self, stack_name: str, amount: int) -> int:
        if amount <= 0:
            return 0
        timers = self.stack_ttls.get(stack_name)
        if not timers:
            return 0
        timers.sort()
        consumed = min(amount, len(timers))
        del timers[:consumed]
        if not timers:
            self.stack_ttls.pop(stack_name, None)
        return consumed

    def clear_timed_stack(self, stack_name: str) -> None:
        self.stack_ttls.pop(stack_name, None)

    def tick_stack_ttls(self) -> None:
        for stack_name in list(self.stack_ttls.keys()):
            timers = self.stack_ttls.get(stack_name, [])
            if not timers:
                self.stack_ttls.pop(stack_name, None)
                continue

            expired = 0
            for idx in range(len(timers)):
                timers[idx] -= 1
                if timers[idx] <= 0:
                    expired += 1

            if expired > 0:
                timers[:] = [value for value in timers if value > 0]
                self.stacks[stack_name] = max(0, self.stacks.get(stack_name, 0) - expired)

            if not timers:
                self.stack_ttls.pop(stack_name, None)

    def get_status_modifier(self, key: str) -> float:
        total = 0.0
        for status in self.statuses:
            data = status.data or {}

            direct = data.get(key)
            if isinstance(direct, (int, float)):
                total += float(direct)

            modifiers = data.get("modifiers")
            if isinstance(modifiers, dict):
                mod_value = modifiers.get(key)
                if isinstance(mod_value, (int, float)):
                    total += float(mod_value)

            legacy_per_stack = data.get(f"{key}_per_stack")
            if isinstance(legacy_per_stack, dict):
                for stack_name, value in legacy_per_stack.items():
                    if isinstance(value, (int, float)):
                        total += float(value) * self.stacks.get(stack_name, 0)

            modifiers_per_stack = data.get("modifiers_per_stack")
            if isinstance(modifiers_per_stack, dict):
                stack_map = modifiers_per_stack.get(key)
                if isinstance(stack_map, dict):
                    for stack_name, value in stack_map.items():
                        if isinstance(value, (int, float)):
                            total += float(value) * self.stacks.get(stack_name, 0)

        return total

    def compute_final_atk(self) -> float:
        atk_bonus = sum(b.value for b in self.buffs if b.name == "atk_buff")
        status_mult = self.get_status_modifier("atk_mult")
        return self.atk * max(0.0, (1.0 + atk_bonus + status_mult))

    def compute_final_speed(self) -> int:
        speed_bonus = int(sum(b.value for b in self.buffs if b.name == "speed_buff"))
        status_speed = int(self.get_status_modifier("speed_delta"))
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
