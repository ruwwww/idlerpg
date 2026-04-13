from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from .effects import EffectExecutor
from .models import Effect, EffectContext, Hero, Team
from .targeting import TargetResolver
from .utils import hero_tag


class BattleEngine:
    def __init__(self, team1: Team, team2: Team):
        self.team1 = team1
        self.team2 = team2
        self.team1.opposite = self.team2
        self.team2.opposite = self.team1

        self.all_heroes: List[Hero] = self.team1.heroes + self.team2.heroes
        self.round = 0
        self.listeners: List[Dict[str, Any]] = []

        self.target_resolver = TargetResolver()
        self.executor = EffectExecutor(self)

    def pick_default_target(self, caster: Hero) -> Optional[Hero]:
        enemies = [hero for hero in caster.team.opposite.heroes if hero.is_alive]
        if not enemies:
            return None

        # Tag-based taunt/forced target logic.
        force_target_status = next((status for status in caster.statuses if status.data.get("force_target_source")), None)
        if force_target_status and force_target_status.source_name:
            for enemy in enemies:
                if enemy.name == force_target_status.source_name:
                    return enemy

        # Tag-based confusion/allies-target logic.
        ally_target_status = next((status for status in caster.statuses if status.data.get("target_allies")), None)
        if ally_target_status:
            allies = [hero for hero in caster.team.heroes if hero.is_alive and hero != caster]
            if allies:
                return random.choice(allies)

        return min(enemies, key=lambda hero: hero.hp)

    def _is_disabled(self, hero: Hero) -> bool:
        return any("disable" in status.tags for status in hero.statuses)

    def _hook_order_key(self, hook: Dict[str, Any]):
        timing = str(hook.get("timing", "normal")).lower()
        timing_rank = {"pre": 0, "before": 0, "normal": 1, "post": 2, "after": 2}.get(timing, 1)
        priority = int(hook.get("priority", 100))
        return (timing_rank, priority)

    def _trigger_status_hooks(self, event_name: str, owner: Hero):
        for status in owner.statuses[:]:
            hooks = status.hooks.get(event_name, [])
            if not hooks:
                continue
            source = self.find_hero_by_name(status.source_name) or owner
            ordered = sorted(hooks, key=self._hook_order_key)
            nested = [
                Effect(
                    hook["type"],
                    **{key: value for key, value in hook.items() if key not in ["type", "priority", "timing"]},
                )
                for hook in ordered
            ]
            self.executor.execute_list(
                nested,
                EffectContext(self, source, [owner], event_name, self.round, {"status": status, "owner": owner}),
            )

    def find_hero_by_name(self, name: Optional[str]) -> Optional[Hero]:
        if not name:
            return None
        for hero in self.all_heroes:
            if hero.name == name and hero.is_alive:
                return hero
        return None

    def emit_event(self, event_name: str, caster: Hero, targets: List[Hero], metadata: Optional[Dict[str, Any]] = None):
        metadata = dict(metadata or {})
        metadata.setdefault("event_source", caster)
        if targets:
            metadata.setdefault("event_target", targets[0])

        if event_name in ["turn_start", "turn_end", "after_skill", "after_action", "on_basic_hit", "on_create"]:
            trigger_pool = [caster]
        else:
            trigger_pool = [hero for hero in self.all_heroes if hero.is_alive]

        for hero in trigger_pool:
            if not hero.is_alive:
                continue
            for passive in hero.passives:
                if passive.trigger_event != event_name:
                    continue
                effects = [Effect(effect.type, **effect.params) for effect in passive.effects]
                self.executor.execute_list(effects, EffectContext(self, hero, targets, event_name, self.round, metadata))

        for listener in self.listeners[:]:
            owner = listener["owner"]
            if not owner.is_alive:
                self.listeners.remove(listener)
                continue
            if listener["event"] != event_name:
                continue
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in listener["effects"]]
            self.executor.execute_list(nested, EffectContext(self, owner, targets, event_name, self.round, metadata))
            listener["duration"] -= 1
            if listener["duration"] <= 0:
                self.listeners.remove(listener)

        for hero in self.all_heroes:
            if hero.is_alive:
                self._trigger_status_hooks(f"on_{event_name}", hero)

    def execute_basic(self, caster: Hero):
        override = caster.behavior.get("basic_override")
        if override and override.get("until_round", -1) >= self.round:
            payload = override["value"]
            effect = Effect(
                "damage",
                mult=payload.get("mult", 1.0),
                target=payload.get("target", "lowest_hp_enemy"),
                shield_steal_pct=payload.get("shield_steal_pct", 0.0),
            )
            self.executor.execute_effect(effect, EffectContext(self, caster, [], "basic_override", self.round, {}))
            if not payload.get("persistent", False):
                caster.behavior.pop("basic_override", None)
        else:
            target_override = caster.behavior.get("basic_target")
            if target_override and target_override.get("until_round", -1) >= self.round:
                target_def = target_override["value"]
            else:
                target_def = "lowest_hp_enemy"

            self.executor.execute_effect(
                Effect("damage", mult=1.0, target=target_def),
                EffectContext(self, caster, [], "basic", self.round, {}),
            )

        self.emit_event("on_basic_hit", caster, [], {})
        caster.energy = min(999, caster.energy + 50)

    def execute_skill(self, caster: Hero):
        if not caster.active_skill:
            self.execute_basic(caster)
            return

        over = max(0, caster.energy - 100)
        if over > 0:
            print(f"    {hero_tag(caster)} overcharge +{over}%.")
        print(f"    {hero_tag(caster)} cast [{caster.active_skill.name}].")

        effects = [Effect(effect.type, **effect.params) for effect in caster.active_skill.effects]
        self.executor.execute_list(effects, EffectContext(self, caster, [], "skill", self.round, {"overcharge": over}))
        self.emit_event("after_skill", caster, [], {})
        caster.energy = 0

    def tick_round_end(self):
        for hero in self.all_heroes:
            if not hero.is_alive:
                continue

            self.emit_event("turn_end", hero, [hero], {})

            for status in hero.statuses[:]:
                status.duration -= 1
                if status.duration <= 0:
                    hero.statuses.remove(status)

            for buff in hero.buffs[:]:
                buff.duration -= 1
                if buff.duration <= 0:
                    hero.buffs.remove(buff)

            for key in list(hero.behavior.keys()):
                rule = hero.behavior[key]
                if isinstance(rule, dict) and rule.get("until_round", -1) < self.round:
                    hero.behavior.pop(key, None)

        alive_anchor = next((hero for hero in self.all_heroes if hero.is_alive), None)
        if alive_anchor:
            self.emit_event("round_end", alive_anchor, [], {})

    def simulate(self, max_rounds: int = 30):
        print("=== FIGHT START ===")
        for hero in self.all_heroes:
            if hero.is_alive:
                self.emit_event("on_create", hero, [hero], {})

        while (
            any(hero.is_alive for hero in self.team1.heroes)
            and any(hero.is_alive for hero in self.team2.heroes)
            and self.round < max_rounds
        ):
            self.round += 1
            print(f"\nTurn {self.round}")

            order = sorted(self.all_heroes, key=lambda hero: (-hero.compute_final_speed(), self.all_heroes.index(hero)))
            for hero in order:
                if not hero.is_alive:
                    continue
                if not any(enemy.is_alive for enemy in hero.team.opposite.heroes):
                    break

                self.emit_event("turn_start", hero, [hero], {})

                if self._is_disabled(hero):
                    print(f"    {hero_tag(hero)} is disabled and cannot act.")
                    continue

                if hero.energy >= 100:
                    self.execute_skill(hero)
                else:
                    self.execute_basic(hero)

                self.emit_event("after_action", hero, [hero], {})

            self.tick_round_end()

        print("\n=== FIGHT END ===")
        print(f"Team 1 alive: {sum(1 for hero in self.team1.heroes if hero.is_alive)}")
        print(f"Team 2 alive: {sum(1 for hero in self.team2.heroes if hero.is_alive)}")
