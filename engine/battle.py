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
        self.action_damaged_targets: List[Hero] = []

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
        return len(self._disable_status_names(hero)) > 0

    def _disable_status_names(self, hero: Hero) -> List[str]:
        out: List[str] = []
        for status in hero.statuses:
            # Taunt should only force target selection, not skip turns.
            if status.data.get("force_target_source", False):
                continue
            if "disable" in status.tags:
                out.append(status.name)
        return out

    def _hook_order_key(self, hook: Dict[str, Any]):
        timing = str(hook.get("timing", "normal")).lower()
        timing_rank = {"pre": 0, "before": 0, "normal": 1, "post": 2, "after": 2}.get(timing, 1)
        priority = int(hook.get("priority", 100))
        return (timing_rank, priority)

    def _trigger_status_hooks(self, event_name: str, owner: Hero, metadata: Optional[Dict[str, Any]] = None):
        merged_meta = dict(metadata or {})
        merged_meta["owner"] = owner
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
                EffectContext(self, source, [owner], event_name, self.round, merged_meta, status=status),
            )

    def find_hero_by_name(self, name: Optional[str]) -> Optional[Hero]:
        if not name:
            return None
        for hero in self.all_heroes:
            if hero.name == name:
                return hero
        return None

    def emit_event(self, event_name: str, caster: Hero, targets: List[Hero], metadata: Optional[Dict[str, Any]] = None):
        metadata = dict(metadata or {})
        metadata.setdefault("event_source", caster)
        if targets:
            metadata.setdefault("event_target", targets[0])

        if event_name in ["turn_start", "turn_end", "after_action", "on_basic_hit", "on_active_skill_used", "on_create"]:
            trigger_pool = [caster]
        elif event_name == "on_death":
            trigger_pool = list(self.all_heroes)
        elif event_name == "on_ally_receive_cc":
            event_target = metadata.get("event_target")
            if event_target is not None and getattr(event_target, "team", None) is not None:
                trigger_pool = [hero for hero in event_target.team.heroes if hero.is_alive]
            else:
                trigger_pool = [hero for hero in self.all_heroes if hero.is_alive]
        else:
            trigger_pool = [hero for hero in self.all_heroes if hero.is_alive]

        for hero in trigger_pool:
            allow_dead_owner = event_name == "on_death" and hero == metadata.get("event_target")
            if not hero.is_alive and not allow_dead_owner:
                continue
            for passive in hero.passives:
                if passive.trigger_event != event_name:
                    continue
                passive_meta = dict(metadata)
                passive_meta["source_skill"] = passive.name
                effects = [Effect(effect.type, **effect.params) for effect in passive.effects]
                self.executor.execute_list(effects, EffectContext(self, hero, targets, event_name, self.round, passive_meta))

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

        for hero in trigger_pool:
            allow_dead_owner = event_name == "on_death" and hero == metadata.get("event_target")
            if hero.is_alive or allow_dead_owner:
                hook_name = event_name if event_name.startswith("on_") else f"on_{event_name}"
                self._trigger_status_hooks(hook_name, hero, metadata)

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
            if caster.basic_skill:
                effects = [Effect(effect.type, **effect.params) for effect in caster.basic_skill.effects]
                self.executor.execute_list(
                    effects,
                    EffectContext(
                        self,
                        caster,
                        [],
                        "basic",
                        self.round,
                        {"source_skill": caster.basic_skill.name},
                    ),
                )
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

        basic_targets = list(dict.fromkeys(self.action_damaged_targets))
        basic_meta = {
            "event_source": caster,
            "action_type": "basic",
        }
        if basic_targets:
            basic_meta["event_target"] = basic_targets[0]
        self.emit_event("on_basic_hit", caster, basic_targets, basic_meta)
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
        self.executor.execute_list(effects, EffectContext(self, caster, [], "skill", self.round, {"overcharge": over, "source_skill": caster.active_skill.name}))
        self.emit_event("on_active_skill_used", caster, self.action_damaged_targets, {"action_type": "skill", "source_skill": caster.active_skill.name})
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
                    data = status.data if isinstance(status.data, dict) else {}
                    pool = data.get("temporary_shield_pool")
                    if isinstance(pool, (int, float)) and pool > 0 and hero.shield > 0:
                        removed = min(hero.shield, float(pool))
                        hero.shield = max(0.0, hero.shield - removed)
                        if removed > 0:
                            print(f"    {hero_tag(hero)} temporary shield expired for {removed:.0f}.")
                    hero.statuses.remove(status)

            for buff in hero.buffs[:]:
                buff.duration -= 1
                if buff.duration <= 0:
                    hero.buffs.remove(buff)

            hero.tick_stack_ttls()

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
                    reasons = ", ".join(self._disable_status_names(hero))
                    if reasons:
                        print(f"    {hero_tag(hero)} is disabled by [{reasons}] and cannot act.")
                    else:
                        print(f"    {hero_tag(hero)} is disabled and cannot act.")
                    continue

                self.action_damaged_targets.clear()

                action_type = "basic"
                if hero.energy >= 100:
                    self.execute_skill(hero)
                    action_type = "skill"
                else:
                    self.execute_basic(hero)

                for target in list(dict.fromkeys(self.action_damaged_targets)):
                    if target.is_alive:
                        self.emit_event(
                            "on_target_action_end",
                            hero,
                            [target],
                            {
                                "event_source": hero,
                                "event_target": target,
                                "action_type": action_type,
                            }
                        )

                self.emit_event("after_action", hero, [hero], {})

            self.tick_round_end()

        print("\n=== FIGHT END ===")
        print(f"Team 1 alive: {sum(1 for hero in self.team1.heroes if hero.is_alive)}")
        print(f"Team 2 alive: {sum(1 for hero in self.team2.heroes if hero.is_alive)}")

        print("\n=== POST BATTLE SUMMARY ===")
        all_heroes_sorted = sorted(self.all_heroes, key=lambda h: (h.team.number, h.name))
        
        current_team = None
        for hero in all_heroes_sorted:
            if current_team != hero.team.number:
                current_team = hero.team.number
                print(f"\n--- Team {current_team} ---")
            
            stats = hero.combat_stats
            print(f"[{hero.name}]")
            print(f"    Damage Dealt (HP):     {stats['damage_dealt_hp']:>8.0f}")
            print(f"    Damage Dealt (Shield): {stats['damage_dealt_shield']:>8.0f}")
            print(f"    Damage Taken (HP):     {stats['damage_taken_hp']:>8.0f}")
            print(f"    Damage Taken (Shield): {stats['damage_taken_shield']:>8.0f}")
            print(f"    Healing Done (Raw):    {stats['healing_done_raw']:>8.0f}")
            print(f"    Healing Done (Eff):    {stats['healing_done']:>8.0f}")
            print(f"    Shielding Done:        {stats['shielding_done']:>8.0f}")
