import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from content_loader import JsonHeroContentSource
from hero_factory import HeroRuntimeFactory


def _hero_tag(hero: "Hero") -> str:
    color = "\033[92m" if getattr(hero.team, "number", 1) == 1 else "\033[91m"
    reset = "\033[0m"
    return f"{color}[{hero.name}]{reset}"


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


class Team:
    def __init__(self, heroes: List[Hero], number: int):
        self.heroes = heroes
        self.number = number
        self.opposite = None
        for h in heroes:
            h.team = self


class TargetResolver:
    def resolve(self, battle: "BattleEngine", caster: Hero, target_def: Any, ctx: EffectContext) -> List[Hero]:
        if target_def is None:
            return [battle.pick_default_target(caster)] if battle.pick_default_target(caster) else []

        if isinstance(target_def, str):
            target_def = {"selector": target_def}

        selector = target_def.get("selector")
        n = int(target_def.get("n", 1))

        enemies = [h for h in caster.team.opposite.heroes if h.is_alive]
        allies = [h for h in caster.team.heroes if h.is_alive]

        if selector == "self":
            return [caster]
        if selector == "all_enemies":
            return enemies
        if selector == "all_allies":
            return allies
        if selector == "random_enemies":
            return random.sample(enemies, min(n, len(enemies))) if enemies else []
        if selector == "random_allies":
            pool = [h for h in allies if h != caster] or allies
            return random.sample(pool, min(n, len(pool))) if pool else []
        if selector == "lowest_hp_enemy":
            return [min(enemies, key=lambda h: h.hp / max(1, h.max_hp))] if enemies else []
        if selector == "highest_atk_enemies":
            return sorted(enemies, key=lambda h: h.atk, reverse=True)[:n]
        if selector == "random_top_atk_enemies":
            top_n = int(target_def.get("top_n", 3))
            top = sorted(enemies, key=lambda h: h.atk, reverse=True)[:top_n]
            return random.sample(top, min(n, len(top))) if top else []
        if selector == "marked_target":
            mark = target_def.get("status", "abyssal_eyes")
            for e in enemies:
                if e.get_status(mark):
                    return [e]
            return []
        if selector == "marked_plus_random_enemy":
            mark = target_def.get("status", "abyssal_eyes")
            marked = None
            for e in enemies:
                if e.get_status(mark):
                    marked = e
                    break
            remaining = [e for e in enemies if e != marked]
            random_enemy = random.choice(remaining) if remaining else None
            out = []
            if marked:
                out.append(marked)
            if random_enemy:
                out.append(random_enemy)
            return out
        if selector == "marked_and_top_atk_others":
            mark = target_def.get("status", "abyssal_eyes")
            marked = None
            for e in enemies:
                if e.get_status(mark):
                    marked = e
                    break
            remaining = [e for e in enemies if e != marked]
            top_others = sorted(remaining, key=lambda h: h.atk, reverse=True)[:n]
            out = []
            if marked:
                out.append(marked)
            out.extend(top_others)
            return out

        return [battle.pick_default_target(caster)] if battle.pick_default_target(caster) else []


class EffectExecutor:
    def __init__(self, battle: "BattleEngine"):
        self.battle = battle
        self.handlers: Dict[str, Callable[[Effect, EffectContext], None]] = {}
        self._register_default_handlers()

    def _register(self, effect_type: str, handler: Callable[[Effect, EffectContext], None]):
        self.handlers[effect_type] = handler

    def execute_effect(self, effect: Effect, ctx: EffectContext):
        handler = self.handlers.get(effect.type)
        if not handler:
            return
        handler(effect, ctx)

    def execute_list(self, effects: List[Effect], ctx: EffectContext):
        for effect in effects:
            self.execute_effect(effect, ctx)

    def _resolve_targets(self, effect: Effect, ctx: EffectContext) -> List[Hero]:
        target_def = effect.params.get("target")

        # Backward compatibility with old JSON keys.
        if effect.params.get("target_self"):
            target_def = "self"
        elif effect.params.get("target_all_enemies"):
            target_def = "all_enemies"
        elif effect.params.get("target_1_random_enemy"):
            target_def = {"selector": "random_enemies", "n": 1}
        elif effect.params.get("target_2_random_enemies"):
            target_def = {"selector": "random_enemies", "n": 2}
        elif effect.params.get("target_3_random_enemies"):
            target_def = {"selector": "random_enemies", "n": 3}
        elif effect.params.get("target_2_random_allies"):
            target_def = {"selector": "random_allies", "n": 2}
        elif effect.params.get("target_lowest_hp"):
            target_def = "lowest_hp_enemy"

        return self.battle.target_resolver.resolve(self.battle, ctx.caster, target_def, ctx)

    def _condition_true(self, condition: Dict[str, Any], ctx: EffectContext) -> bool:
        ctype = condition.get("type")

        if ctype == "random_chance":
            return random.random() < float(condition.get("chance", 0.0))

        target_selector = condition.get("target", "self")
        targets = self.battle.target_resolver.resolve(self.battle, ctx.caster, target_selector, ctx)
        if not targets:
            return False
        target = targets[0]

        if ctype == "hp_pct_below":
            return (target.hp / max(1, target.max_hp)) < float(condition.get("value", 0.5))

        if ctype == "stack_lte":
            return target.stacks.get(condition.get("stack", ""), 0) <= int(condition.get("value", 0))

        if ctype == "stack_gte":
            return target.stacks.get(condition.get("stack", ""), 0) >= int(condition.get("value", 0))

        if ctype == "status_exists":
            return target.get_status(condition.get("status", "")) is not None

        return False

    def _apply_damage(self, target: Hero, amount: float, caster: Hero, is_crit: bool):
        amount = max(0.0, amount)

        dr = sum(s.data.get("damage_reduction", 0.0) for s in target.statuses)
        if dr > 0:
            amount *= max(0.0, 1.0 - dr)

        dtu = sum(s.data.get("damage_taken_up", 0.0) for s in target.statuses)
        if dtu > 0:
            amount *= 1.0 + dtu

        if amount > 0 and target.shield > 0:
            absorbed = min(target.shield, amount)
            target.shield -= absorbed
            amount -= absorbed
            print(f"    {_hero_tag(target)}'s shield absorbed {absorbed:.0f} damage (Remaining: {target.shield:.0f}).")

        if amount > 0:
            target.hp -= amount
            print(f"    {_hero_tag(caster)} hit {_hero_tag(target)} for {amount:.0f}{' (CRIT)' if is_crit else ''}.")
            print(f"    {_hero_tag(target)} now has {max(0, target.hp):.0f}/{target.max_hp:.0f} HP.")
            if target.hp <= 0:
                target.is_alive = False
                print(f"    {_hero_tag(target)} has been defeated.")
                self.battle.emit_event("on_death", caster, [target], {"dead": target})

        target.energy = min(999, target.energy + (20 if is_crit else 10))

    def _register_default_handlers(self):
        def h_damage(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            mult = float(effect.params.get("mult", 1.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                dmg = ctx.caster.compute_final_atk() * mult
                hp_threshold_pct = effect.params.get("hp_threshold_pct")
                if hp_threshold_pct is not None:
                    if (target.hp / max(1, target.max_hp)) < (float(hp_threshold_pct) / 100.0):
                        dmg *= float(effect.params.get("hp_threshold_mult", 1.0))

                is_crit = random.random() < max(0.0, min(1.0, ctx.caster.crit_chance))
                if is_crit:
                    dmg *= ctx.caster.crit_damage

                self._apply_damage(target, dmg, ctx.caster, is_crit)

                shield_steal_pct = float(effect.params.get("shield_steal_pct", 0.0))
                if shield_steal_pct > 0 and dmg > 0:
                    gain = dmg * shield_steal_pct
                    old = ctx.caster.shield
                    ctx.caster.shield = min(ctx.caster.max_shield, ctx.caster.shield + gain)
                    print(f"    {_hero_tag(ctx.caster)} gained {ctx.caster.shield - old:.0f} shield.")

        def h_heal(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            mult = float(effect.params.get("mult", 1.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                amount = ctx.caster.compute_final_atk() * mult
                target.hp = min(target.max_hp, target.hp + amount)
                print(f"    {_hero_tag(ctx.caster)} healed {_hero_tag(target)} for {amount:.0f}.")

        def h_modify_stat(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stat_type = effect.params.get("stat_type")
            add = effect.params.get("add")
            mult = effect.params.get("mult")
            for target in targets:
                if stat_type == "max_hp":
                    if mult is not None:
                        target.max_hp *= float(mult)
                        target.hp = min(target.hp, target.max_hp)
                        print(f"    {_hero_tag(target)} max HP changed to {target.max_hp:.0f}.")
                    continue
                if not hasattr(target, stat_type):
                    continue
                current = getattr(target, stat_type)
                if mult is not None:
                    current *= float(mult)
                if add is not None:
                    current += float(add)
                if stat_type == "energy":
                    current = min(999, max(0, current))
                if stat_type == "hp":
                    current = min(target.max_hp, max(0, current))
                setattr(target, stat_type, current)
                print(f"    {_hero_tag(target)} {stat_type} is now {current}.")

        def h_apply_status(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            status_name = effect.params.get("status") or effect.params.get("cc_type")
            duration = int(effect.params.get("duration", 1))
            tags = list(effect.params.get("tags", []))
            data = dict(effect.params.get("data", {}))
            hooks = dict(effect.params.get("hooks", {}))

            # Compatibility: convert old taunt damage reduction field.
            if effect.params.get("damage_reduction_pct") is not None and status_name == "taunt":
                data["taunt_damage_reduction_pct"] = effect.params.get("damage_reduction_pct")

            for target in targets:
                if not target or not target.is_alive:
                    continue

                if status_name in ["stun", "freeze", "taunt", "confusion"] and target.get_status("cc_immunity"):
                    print(f"    {_hero_tag(target)} blocked {status_name} with CC Immunity.")
                    target.statuses = [s for s in target.statuses if s.name != "cc_immunity"]
                    continue

                existing = target.get_status(status_name)
                if existing:
                    existing.duration = max(existing.duration, duration)
                    existing.stacks += int(effect.params.get("stacks", 1))
                    continue

                status = Status(
                    name=status_name,
                    duration=duration,
                    stacks=int(effect.params.get("stacks", 1)),
                    tags=tags,
                    data=data,
                    hooks=hooks,
                    source_name=ctx.caster.name,
                )
                target.statuses.append(status)
                print(f"    {_hero_tag(target)} gained status {status_name} ({duration} rounds).")

                # Backward compatibility: old CC-dispel reaction.
                if status_name in ["stun", "freeze", "taunt", "seal_of_light"]:
                    self.battle.emit_event(
                        "on_ally_receive_cc",
                        ctx.caster,
                        [target],
                        {"target": target, "cc_type": status_name},
                    )

        def h_remove_status(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            status_name = effect.params.get("status")
            tag = effect.params.get("tag")
            for target in targets:
                if not target:
                    continue
                before = len(target.statuses)
                if status_name:
                    target.statuses = [s for s in target.statuses if s.name != status_name]
                elif tag:
                    target.statuses = [s for s in target.statuses if tag not in s.tags]
                if len(target.statuses) < before:
                    print(f"    {_hero_tag(target)} had status removed.")

        def h_add_stack(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stack_name = effect.params.get("stack")
            amount = int(effect.params.get("amount", 1))
            min_value = effect.params.get("min")
            max_value = effect.params.get("max")
            for target in targets:
                target.stacks[stack_name] += amount
                if min_value is not None:
                    target.stacks[stack_name] = max(int(min_value), target.stacks[stack_name])
                if max_value is not None:
                    target.stacks[stack_name] = min(int(max_value), target.stacks[stack_name])
                print(f"    {_hero_tag(target)} stack {stack_name} = {target.stacks[stack_name]}.")

        def h_set_stack(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stack_name = effect.params.get("stack")
            value = int(effect.params.get("value", 0))
            for target in targets:
                target.stacks[stack_name] = max(0, value)
                print(f"    {_hero_tag(target)} stack {stack_name} set to {target.stacks[stack_name]}.")

        def h_consume_stack(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stack_name = effect.params.get("stack")
            amount = int(effect.params.get("amount", 1))
            for target in targets:
                target.stacks[stack_name] = max(0, target.stacks.get(stack_name, 0) - amount)
                print(f"    {_hero_tag(target)} consumed {amount} {stack_name} stack(s).")

        def h_sequence(effect: Effect, ctx: EffectContext):
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in effect.params.get("effects", [])]
            self.execute_list(nested, ctx)

        def h_conditional(effect: Effect, ctx: EffectContext):
            condition = effect.params.get("condition", {})
            branch = effect.params.get("then", []) if self._condition_true(condition, ctx) else effect.params.get("else", [])
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in branch]
            self.execute_list(nested, ctx)

        def h_repeat(effect: Effect, ctx: EffectContext):
            times = int(effect.params.get("times", 1))
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in effect.params.get("effects", [])]
            for _ in range(max(0, times)):
                self.execute_list(nested, ctx)

        def h_heal_max_hp_pct(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            pct = float(effect.params.get("pct", 0.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                amount = target.max_hp * pct
                target.hp = min(target.max_hp, target.hp + amount)
                print(f"    {_hero_tag(target)} recovered {amount:.0f} HP ({pct*100:.0f}% max HP).")

        def h_random_choice(effect: Effect, ctx: EffectContext):
            choices = effect.params.get("choices", [])
            if not choices:
                return
            picked = random.choice(choices)
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in picked.get("effects", [])]
            self.execute_list(nested, ctx)

        def h_trigger_event(effect: Effect, ctx: EffectContext):
            event_name = effect.params.get("event")
            event_target = self.battle.target_resolver.resolve(self.battle, ctx.caster, effect.params.get("target", "self"), ctx)
            self.battle.emit_event(event_name, ctx.caster, event_target, dict(effect.params.get("metadata", {})))

        def h_listen_event(effect: Effect, ctx: EffectContext):
            listener = {
                "owner": ctx.caster,
                "event": effect.params.get("event"),
                "effects": effect.params.get("effects", []),
                "duration": int(effect.params.get("duration", 9999)),
            }
            self.battle.listeners.append(listener)

        def h_modify_behavior(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            key = effect.params.get("behavior")
            value = effect.params.get("value")
            duration = int(effect.params.get("duration", 1))
            for target in targets:
                target.behavior[key] = {"value": value, "until_round": self.battle.round + duration}
                print(f"    {_hero_tag(target)} behavior {key} modified for {duration} rounds.")

        def h_apply_dot(effect: Effect, ctx: EffectContext):
            # Generic DoT implemented as status with turn-end hook.
            dot_status = Effect(
                "apply_status",
                status=effect.params.get("status", "dot"),
                duration=int(effect.params.get("duration", 2)),
                tags=["dot", "debuff"],
                hooks={
                    "on_turn_end": [
                        {
                            "type": "damage",
                            "mult": float(effect.params.get("mult", 0.3)),
                            "target": "self",
                        }
                    ]
                },
            )
            h_apply_status(dot_status, ctx)

        # Compatibility handlers for old effects.
        def h_apply_cc(effect: Effect, ctx: EffectContext):
            mapped = Effect(
                "apply_status",
                status=effect.params.get("cc_type", "stun"),
                duration=effect.params.get("duration", 1),
                damage_reduction_pct=effect.params.get("damage_reduction_pct", 0),
                target=effect.params.get("target"),
                target_self=effect.params.get("target_self"),
                target_all_enemies=effect.params.get("target_all_enemies"),
                target_1_random_enemy=effect.params.get("target_1_random_enemy"),
                target_2_random_enemies=effect.params.get("target_2_random_enemies"),
                target_3_random_enemies=effect.params.get("target_3_random_enemies"),
                target_lowest_hp=effect.params.get("target_lowest_hp"),
            )
            h_apply_status(mapped, ctx)

        def h_apply_cc_immunity(effect: Effect, ctx: EffectContext):
            mapped = Effect(
                "apply_status",
                status="cc_immunity",
                duration=effect.params.get("duration", 2),
                target=effect.params.get("target"),
                target_2_random_allies=effect.params.get("target_2_random_allies"),
                tags=["buff"],
            )
            h_apply_status(mapped, ctx)

        def h_modify_heal(effect: Effect, ctx: EffectContext):
            status = Effect(
                "apply_status",
                status="heal_invert",
                duration=999,
                target="self",
                tags=["special"],
            )
            h_apply_status(status, ctx)

        def h_override_basic(effect: Effect, ctx: EffectContext):
            behavior = Effect(
                "modify_behavior",
                behavior="basic_override",
                value={
                    "is_damage": bool(effect.params.get("is_damage", effect.params.get("convert_to_damage", False))),
                    "mult": float(effect.params.get("mult", 1.0)),
                    "target": effect.params.get("target") or (
                        {"selector": "random_enemies", "n": 2} if effect.params.get("target_2_random_enemies") else "all_allies"
                    ),
                    "shield_steal_pct": float(effect.params.get("shield_steal_pct", 0.0)),
                    "persistent": bool(effect.params.get("persistent", False)),
                },
                duration=999 if effect.params.get("persistent", False) else 1,
                target="self",
            )
            h_modify_behavior(behavior, ctx)

        def h_angela_dispel(effect: Effect, ctx: EffectContext):
            if random.random() > float(effect.params.get("chance", 0.3)):
                return
            target = ctx.metadata.get("target")
            if not isinstance(target, Hero):
                return
            cc_type = ctx.metadata.get("cc_type")
            if cc_type:
                target.statuses = [s for s in target.statuses if s.name != cc_type]
            heal_mult = float(effect.params.get("heal_mult", 1.5))
            heal_effect = Effect("heal", mult=heal_mult, target={"selector": "self"})
            self.execute_effect(heal_effect, EffectContext(self.battle, ctx.caster, [target], ctx.event, ctx.round, ctx.metadata))
            print(f"    {_hero_tag(ctx.caster)} dispelled {cc_type} from {_hero_tag(target)}.")

        def h_apply_shield_resonance(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            for target in targets:
                target.statuses.append(
                    Status(
                        name="shield_resonance",
                        duration=999,
                        tags=["buff"],
                        data={
                            "shield_dr": float(effect.params.get("dr_pct", 5)) / 100.0,
                            "shield_cc_resist": float(effect.params.get("cc_resist_pct", 10)) / 100.0,
                        },
                        source_name=ctx.caster.name,
                    )
                )

        # Register handlers.
        self._register("damage", h_damage)
        self._register("heal", h_heal)
        self._register("apply_status", h_apply_status)
        self._register("remove_status", h_remove_status)
        self._register("add_stack", h_add_stack)
        self._register("set_stack", h_set_stack)
        self._register("consume_stack", h_consume_stack)
        self._register("modify_stat", h_modify_stat)
        self._register("sequence", h_sequence)
        self._register("conditional", h_conditional)
        self._register("repeat", h_repeat)
        self._register("heal_max_hp_pct", h_heal_max_hp_pct)
        self._register("random_choice", h_random_choice)
        self._register("trigger_event", h_trigger_event)
        self._register("listen_event", h_listen_event)
        self._register("modify_behavior", h_modify_behavior)
        self._register("apply_dot", h_apply_dot)

        # Compatibility.
        self._register("apply_cc", h_apply_cc)
        self._register("apply_cc_immunity", h_apply_cc_immunity)
        self._register("modify_heal", h_modify_heal)
        self._register("override_basic", h_override_basic)
        self._register("angela_dispel", h_angela_dispel)
        self._register("apply_shield_resonance", h_apply_shield_resonance)


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
        enemies = [h for h in caster.team.opposite.heroes if h.is_alive]
        if not enemies:
            return None

        taunt = caster.get_status("taunt")
        if taunt:
            taunter_name = taunt.source_name
            for enemy in enemies:
                if enemy.name == taunter_name:
                    return enemy

        confusion = caster.get_status("confusion")
        if confusion:
            allies = [h for h in caster.team.heroes if h.is_alive and h != caster]
            if allies:
                return random.choice(allies)

        return min(enemies, key=lambda h: h.hp)

    def _is_disabled(self, hero: Hero) -> bool:
        return hero.get_status("stun") is not None or hero.get_status("freeze") is not None

    def _trigger_status_hooks(self, event_name: str, owner: Hero):
        for status in owner.statuses[:]:
            hooks = status.hooks.get(event_name, [])
            if not hooks:
                continue
            source = self.find_hero_by_name(status.source_name) or owner
            nested = [Effect(e["type"], **{k: v for k, v in e.items() if k != "type"}) for e in hooks]
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
        metadata = metadata or {}

        # Passive triggers.
        if event_name == "turn_start":
            trigger_pool = [caster]
        elif event_name == "turn_end":
            trigger_pool = [caster]
        elif event_name in ["after_skill", "after_action", "on_basic_hit", "on_create"]:
            trigger_pool = [caster]
        else:
            trigger_pool = [h for h in self.all_heroes if h.is_alive]

        for hero in trigger_pool:
            if not hero.is_alive:
                continue
            for passive in hero.passives:
                if passive.trigger_event != event_name:
                    continue
                effects = [Effect(e.type, **e.params) for e in passive.effects]
                self.executor.execute_list(effects, EffectContext(self, hero, targets, event_name, self.round, metadata))

        # Listener triggers.
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

        # Status hooks.
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
            print(f"    {_hero_tag(caster)} overcharge +{over}%.")
        print(f"    {_hero_tag(caster)} cast [{caster.active_skill.name}].")
        effects = [Effect(e.type, **e.params) for e in caster.active_skill.effects]
        self.executor.execute_list(effects, EffectContext(self, caster, [], "skill", self.round, {"overcharge": over}))
        self.emit_event("after_skill", caster, [], {})
        caster.energy = 0

    def tick_round_end(self):
        for hero in self.all_heroes:
            if not hero.is_alive:
                continue

            self.emit_event("turn_end", hero, [hero], {})

            # Decay statuses.
            for status in hero.statuses[:]:
                status.duration -= 1
                if status.duration <= 0:
                    hero.statuses.remove(status)

            # Decay buffs.
            for b in hero.buffs[:]:
                b.duration -= 1
                if b.duration <= 0:
                    hero.buffs.remove(b)

            # Clear expired behavior overrides.
            for key in list(hero.behavior.keys()):
                rule = hero.behavior[key]
                if isinstance(rule, dict) and rule.get("until_round", -1) < self.round:
                    hero.behavior.pop(key, None)

        alive_anchor = next((h for h in self.all_heroes if h.is_alive), None)
        if alive_anchor:
            self.emit_event("round_end", alive_anchor, [], {})

    def simulate(self, max_rounds: int = 30):
        print("=== FIGHT START ===")
        for hero in self.all_heroes:
            if hero.is_alive:
                self.emit_event("on_create", hero, [hero], {})

        while (
            any(h.is_alive for h in self.team1.heroes)
            and any(h.is_alive for h in self.team2.heroes)
            and self.round < max_rounds
        ):
            self.round += 1
            print(f"\nTurn {self.round}")

            order = sorted(self.all_heroes, key=lambda h: (-h.compute_final_speed(), self.all_heroes.index(h)))
            for hero in order:
                if not hero.is_alive:
                    continue
                if not any(e.is_alive for e in hero.team.opposite.heroes):
                    break

                self.emit_event("turn_start", hero, [hero], {})

                if self._is_disabled(hero):
                    print(f"    {_hero_tag(hero)} is disabled and cannot act.")
                    continue

                if hero.energy >= 100:
                    self.execute_skill(hero)
                else:
                    self.execute_basic(hero)

                self.emit_event("after_action", hero, [hero], {})

            self.tick_round_end()

        print("\n=== FIGHT END ===")
        print(f"Team 1 alive: {sum(1 for h in self.team1.heroes if h.is_alive)}")
        print(f"Team 2 alive: {sum(1 for h in self.team2.heroes if h.is_alive)}")


def build_default_teams() -> tuple[Team, Team]:
    content_path = Path(__file__).parent / "data" / "game_content.json"
    source = JsonHeroContentSource(str(content_path))

    # Validation against registered handlers only.
    probe_engine = BattleEngine(Team([], 1), Team([], 2))
    source.validate_effect_types(set(probe_engine.executor.handlers.keys()))

    runtime_factory = HeroRuntimeFactory(source, Hero, Skill, Passive, Effect)
    team1 = Team(runtime_factory.create_team_heroes("team1_default"), 1)
    team2 = Team(runtime_factory.create_team_heroes("team2_default"), 2)
    team1.opposite = team2
    team2.opposite = team1
    return team1, team2


if __name__ == "__main__":
    t1, t2 = build_default_teams()
    engine = BattleEngine(t1, t2)
    engine.simulate(max_rounds=30)
