from __future__ import annotations

import random
from typing import Any, Callable, Dict, List

from .models import Effect, EffectContext, Hero, Status
from .utils import hero_tag


CC_TAGS = {
    "stun": ["cc", "disable"],
    "freeze": ["cc", "disable"],
    "taunt": ["cc", "target_control"],
    "confusion": ["cc", "target_override"],
    "seal_of_light": ["cc"],
}

CC_DATA = {
    "taunt": {"force_target_source": True},
    "confusion": {"target_allies": True},
}


class EffectExecutor:
    def __init__(self, battle):
        self.battle = battle
        self.handlers: Dict[str, Callable[[Effect, EffectContext], None]] = {}
        self._register_default_handlers()

    def register(self, effect_type: str, handler: Callable[[Effect, EffectContext], None]):
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

        if ctype == "all":
            return all(self._condition_true(c, ctx) for c in condition.get("conditions", []))

        if ctype == "any":
            return any(self._condition_true(c, ctx) for c in condition.get("conditions", []))

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

        if ctype == "is_event_target":
            meta_target = ctx.metadata.get("event_target")
            return meta_target is not None and meta_target == target

        if ctype == "event_metadata_match":
            key = condition.get("key")
            expected = condition.get("value")
            return ctx.metadata.get(key) == expected

        if ctype == "event_metadata_not_match":
            key = condition.get("key")
            expected = condition.get("value")
            return ctx.metadata.get(key) != expected

        return False

    def _apply_damage(self, target: Hero, amount: float, caster: Hero, is_crit: bool, damage_type: str = "physical", source_skill: Optional[str] = None):
        amount = max(0.0, amount)

        dr = sum(status.data.get("damage_reduction", 0.0) for status in target.statuses)
        for status in target.statuses:
            if "damage_reduction_per_stack" in status.data:
                for stack_name, dr_val in status.data["damage_reduction_per_stack"].items():
                    dr += dr_val * target.stacks.get(stack_name, 0)
        
        if target.shield > 0:
            dr += sum(status.data.get("shield_damage_reduction", 0.0) for status in target.statuses)
        
        if dr > 0:
            amount *= max(0.0, 1.0 - dr)

        if damage_type == "dot":
            dot_dr = sum(status.data.get("dot_damage_reduction", 0.0) for status in target.statuses)
            amount *= max(0.0, 1.0 - dot_dr)

        dtu = sum(status.data.get("damage_taken_up", 0.0) for status in target.statuses)
        if dtu > 0:
            amount *= 1.0 + dtu

        original_amount = amount
        source_str = f"[{source_skill}]" if source_skill else hero_tag(caster)

        if amount > 0 and target.shield > 0:
            absorbed = min(target.shield, amount)
            target.shield -= absorbed
            amount -= absorbed
            target.combat_stats["damage_taken_shield"] += absorbed
            caster.combat_stats["damage_dealt_shield"] += absorbed
            if damage_type == "dot":
                print(f"    {hero_tag(target)} took {original_amount:.0f} DoT damage from {source_str}.")
            else:
                print(f"    {hero_tag(caster)} hit {hero_tag(target)} for {original_amount:.0f}{' (CRIT)' if is_crit else ''}.")
            print(f"    {hero_tag(target)}'s shield absorbed {absorbed:.0f} damage (Remaining: {target.shield:.0f}).")
        elif amount > 0:
            if damage_type == "dot":
                print(f"    {hero_tag(target)} took {amount:.0f} DoT damage from {source_str}.")
            else:
                print(f"    {hero_tag(caster)} hit {hero_tag(target)} for {amount:.0f}{' (CRIT)' if is_crit else ''}.")

        if amount > 0:
            target.hp -= amount
            target.combat_stats["damage_taken_hp"] += amount
            caster.combat_stats["damage_dealt_hp"] += amount
            print(f"    {hero_tag(target)} now has {max(0, target.hp):.0f}/{target.max_hp:.0f} HP.")
            if target.hp <= 0:
                target.is_alive = False
                print(f"    {hero_tag(target)} has been defeated.")
                self.battle.emit_event("on_death", caster, [target], {"dead": target, "event_source": caster, "event_target": target})

        target.energy = min(999, target.energy + (20 if is_crit else 10))

    def _register_default_handlers(self):
        def h_damage(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            mult = float(effect.params.get("mult", 1.0))
            amount_param = effect.params.get("amount")
            no_crit = effect.params.get("no_crit", False)
            for target in targets:
                if not target or not target.is_alive:
                    continue
                if amount_param is not None:
                    if isinstance(amount_param, str) and ctx.status and amount_param.startswith("data."):
                        key = amount_param[5:]
                        dmg = float(ctx.status.data.get(key, 0.0))
                    else:
                        dmg = float(amount_param)
                else:
                    dmg = ctx.caster.compute_final_atk() * mult
                hp_threshold_pct = effect.params.get("hp_threshold_pct")
                if hp_threshold_pct is not None:
                    if (target.hp / max(1, target.max_hp)) < (float(hp_threshold_pct) / 100.0):
                        dmg *= float(effect.params.get("hp_threshold_mult", 1.0))

                is_crit = False
                if not no_crit:
                    is_crit = random.random() < max(0.0, min(1.0, ctx.caster.crit_chance))
                    if is_crit:
                        dmg *= ctx.caster.crit_damage

                source_skill = ctx.status.source_skill if ctx.status else ctx.metadata.get("source_skill")
                self._apply_damage(target, dmg, ctx.caster, is_crit, damage_type=effect.params.get("damage_type", "physical"), source_skill=source_skill)
                ctx.damage_dealt += dmg
                if not effect.params.get("no_counter", False):
                    self.battle.action_damaged_targets.append(target)
                    self.battle.emit_event(
                        "on_receive_damage",
                        ctx.caster,
                        [target],
                        {
                            "target": target,
                            "damage": dmg,
                            "damage_type": effect.params.get("damage_type", "physical"),
                            "event_source": ctx.caster,
                            "event_target": target,
                        }
                    )

                shield_steal_pct = float(effect.params.get("shield_steal_pct", 0.0))
                if shield_steal_pct > 0 and dmg > 0:
                    gain = dmg * shield_steal_pct
                    old = ctx.caster.shield
                    ctx.caster.shield = min(ctx.caster.max_shield, ctx.caster.shield + gain)
                    print(f"    {hero_tag(ctx.caster)} gained {ctx.caster.shield - old:.0f} shield.")

        def h_heal(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            mult = float(effect.params.get("mult", 1.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                amount = ctx.caster.compute_final_atk() * mult
                target.hp = min(target.max_hp, target.hp + amount)
                ctx.caster.combat_stats["healing_done"] += amount
                print(f"    {hero_tag(ctx.caster)} healed {hero_tag(target)} for {amount:.0f}.")

        def h_modify_stat(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stat_type = effect.params.get("stat_type")
            add = effect.params.get("add")
            mult = effect.params.get("mult")
            for target in targets:
                if stat_type == "max_hp":
                    if mult is not None:
                        current_pct = target.hp / max(1.0, target.max_hp)
                        target.max_hp *= float(mult)
                        target.hp = target.max_hp * current_pct
                        target.max_shield = target.max_hp
                        print(f"    {hero_tag(target)} max HP changed to {target.max_hp:.0f}.")
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
                print(f"    {hero_tag(target)} {stat_type} is now {current}.")

        def h_apply_status(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            status_name = effect.params.get("status") or effect.params.get("cc_type")
            duration = int(effect.params.get("duration", 1))
            tags = list(effect.params.get("tags", []))
            data = dict(effect.params.get("data", {}))
            hooks = dict(effect.params.get("hooks", {}))

            if status_name in CC_TAGS:
                tags = list(set(tags + CC_TAGS[status_name]))
                merged = dict(CC_DATA.get(status_name, {}))
                merged.update(data)
                data = merged

            if effect.params.get("damage_reduction_pct") is not None and status_name == "taunt":
                data["taunt_damage_reduction_pct"] = float(effect.params.get("damage_reduction_pct")) / 100.0

            chance = float(effect.params.get("chance", 1.0))

            for target in targets:
                if not target or not target.is_alive:
                    continue

                if chance < 1.0 and random.random() >= chance:
                    continue

                incoming_is_cc = "cc" in tags
                cc_immunity_pct = sum(status.data.get("cc_immunity_chance", 0.0) for status in target.statuses)
                if incoming_is_cc and random.random() < cc_immunity_pct:
                    print(f"    {hero_tag(target)} blocked {status_name} with CC Immunity.")
                    continue

                existing = target.get_status(status_name)
                if existing:
                    existing.duration = max(existing.duration, duration)
                    existing.stacks += int(effect.params.get("stacks", 1))
                    existing.tags = list(set(existing.tags + tags))
                    existing.data.update(data)
                    continue

                status = Status(
                    name=status_name,
                    duration=duration,
                    stacks=int(effect.params.get("stacks", 1)),
                    tags=tags,
                    data=data,
                    hooks=hooks,
                    source_name=ctx.caster.name,
                    source_skill=ctx.metadata.get("source_skill")
                )
                target.statuses.append(status)
                print(f"    {hero_tag(target)} gained status {status_name} ({duration} rounds).")

                if "cc" in status.tags:
                    self.battle.emit_event(
                        "on_ally_receive_cc",
                        ctx.caster,
                        [target],
                        {
                            "target": target,
                            "cc_type": status_name,
                            "event_source": ctx.caster,
                            "event_target": target,
                        },
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
                    target.statuses = [status for status in target.statuses if status.name != status_name]
                elif tag:
                    target.statuses = [status for status in target.statuses if tag not in status.tags]
                if len(target.statuses) < before:
                    print(f"    {hero_tag(target)} had status removed.")

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
                print(f"    {hero_tag(target)} stack {stack_name} = {target.stacks[stack_name]}.")

        def h_set_stack(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stack_name = effect.params.get("stack")
            value_param = effect.params.get("value", 0)
            if isinstance(value_param, str):
                value = int(eval(value_param, {"stacks": ctx.caster.stacks}))
            else:
                value = int(value_param)
            for target in targets:
                target.stacks[stack_name] = max(0, value)
                print(f"    {hero_tag(target)} stack {stack_name} set to {target.stacks[stack_name]}.")

        def h_consume_stack(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            stack_name = effect.params.get("stack")
            amount = int(effect.params.get("amount", 1))
            for target in targets:
                target.stacks[stack_name] = max(0, target.stacks.get(stack_name, 0) - amount)
                print(f"    {hero_tag(target)} consumed {amount} {stack_name} stack(s).")

        def h_add_shield(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            mult = float(effect.params.get("mult", 0.0))
            max_hp_pct = float(effect.params.get("max_hp_pct", 0.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                amount = 0.0
                if mult > 0:
                    amount += ctx.caster.compute_final_atk() * mult
                if max_hp_pct > 0:
                    amount += ctx.caster.max_hp * max_hp_pct
                if amount > 0:
                    target.shield = min(target.max_shield, target.shield + amount)
                    ctx.caster.combat_stats["shielding_done"] += amount
                    print(f"    {hero_tag(target)} gained {amount:.0f} shield (Total: {target.shield:.0f}).")

        self.handlers["add_shield"] = h_add_shield

        def h_sequence(effect: Effect, ctx: EffectContext):
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in effect.params.get("effects", [])]
            self.execute_list(nested, ctx)

        def h_conditional(effect: Effect, ctx: EffectContext):
            condition = effect.params.get("condition", {})
            branch = effect.params.get("then", []) if self._condition_true(condition, ctx) else effect.params.get("else", [])
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in branch]
            self.execute_list(nested, ctx)

        def h_repeat(effect: Effect, ctx: EffectContext):
            times = int(effect.params.get("times", 1))
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in effect.params.get("effects", [])]
            for _ in range(max(0, times)):
                self.execute_list(nested, ctx)

        self.handlers["repeat"] = h_repeat

        def h_repeat_stack_based(effect: Effect, ctx: EffectContext):
            stack_name = effect.params.get("stack")
            base_times = int(effect.params.get("base_times", 0))
            times = base_times + ctx.caster.stacks.get(stack_name, 0)
            reselect_def = effect.params.get("reselect_dead_target")
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in effect.params.get("effects", [])]
            for _ in range(max(0, times)):
                if reselect_def and ctx.targets:
                    # If the current target(s) are dead, re-roll using the provided target def
                    for i in range(len(ctx.targets)):
                        if not ctx.targets[i].is_alive:
                            new_targets = ctx.battle.target_resolver.resolve(ctx.battle, ctx.caster, reselect_def, ctx)
                            if new_targets:
                                ctx.targets[i] = new_targets[0]
                self.execute_list(nested, ctx)

        self.handlers["repeat_stack_based"] = h_repeat_stack_based

        def h_with_target(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            if not targets:
                return
            new_ctx = EffectContext(
                battle=ctx.battle,
                caster=ctx.caster,
                targets=targets,
                event=ctx.event,
                round=ctx.round,
                metadata=ctx.metadata,
                damage_dealt=ctx.damage_dealt,
                status=ctx.status,
            )
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in effect.params.get("effects", [])]
            self.execute_list(nested, new_ctx)
            ctx.damage_dealt = new_ctx.damage_dealt

        self.handlers["with_target"] = h_with_target

        def h_dispel_random_debuff(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            for target in targets:
                debuffs = [status for status in target.statuses if "debuff" in status.tags]
                if debuffs:
                    dispelled = random.choice(debuffs)
                    target.statuses.remove(dispelled)
                    print(f"    {hero_tag(target)} dispelled {dispelled.name}.")
                else:
                    print(f"    {hero_tag(target)} has no debuffs to dispel.")

        self.handlers["dispel_random_debuff"] = h_dispel_random_debuff

        def h_heal_max_hp_pct(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            pct = float(effect.params.get("pct", 0.0))
            for target in targets:
                if not target or not target.is_alive:
                    continue
                amount = target.max_hp * pct
                target.hp = min(target.max_hp, target.hp + amount)
                ctx.caster.combat_stats["healing_done"] += amount
                print(f"    {hero_tag(target)} recovered {amount:.0f} HP ({pct*100:.0f}% max HP).")

        def h_random_choice(effect: Effect, ctx: EffectContext):
            choices = effect.params.get("choices", [])
            if not choices:
                return
            picked = random.choice(choices)
            nested = [Effect(entry["type"], **{k: v for k, v in entry.items() if k != "type"}) for entry in picked.get("effects", [])]
            self.execute_list(nested, ctx)

        def h_trigger_event(effect: Effect, ctx: EffectContext):
            event_name = effect.params.get("event")
            event_targets = self.battle.target_resolver.resolve(self.battle, ctx.caster, effect.params.get("target", "self"), ctx)
            metadata = dict(effect.params.get("metadata", {}))
            if event_targets:
                metadata.setdefault("event_target", event_targets[0])
            metadata.setdefault("event_source", ctx.caster)
            self.battle.emit_event(event_name, ctx.caster, event_targets, metadata)

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
                print(f"    {hero_tag(target)} behavior {key} modified for {duration} rounds.")

        def h_heal_percent_damage_dealt(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            pct = float(effect.params.get("pct", 0.2))
            amount = ctx.damage_dealt * pct
            for target in targets:
                if not target or not target.is_alive:
                    continue
                target.hp = min(target.max_hp, target.hp + amount)
                ctx.caster.combat_stats["healing_done"] += amount
                print(f"    {hero_tag(ctx.caster)} healed {hero_tag(target)} for {amount:.0f} HP ({pct*100:.0f}% of damage dealt).")

        self.handlers["heal_percent_damage_dealt"] = h_heal_percent_damage_dealt

        def h_apply_dot(effect: Effect, ctx: EffectContext):
            dot_status = Effect(
                "apply_status",
                status=effect.params.get("status", "dot"),
                duration=int(effect.params.get("duration", 2)),
                tags=["dot", "debuff"],
                hooks={
                    "on_turn_end": [
                        {
                            "priority": 10,
                            "timing": "normal",
                            "type": "damage",
                            "mult": float(effect.params.get("mult", 0.3)),
                            "target": "owner",
                            "damage_type": "dot",
                            "no_crit": True
                        }
                    ]
                },
            )
            h_apply_status(dot_status, ctx)

        self.handlers["apply_dot"] = h_apply_dot

        def h_apply_dot_percent_damage_dealt(effect: Effect, ctx: EffectContext):
            targets = self._resolve_targets(effect, ctx)
            pct = float(effect.params.get("pct", 0.5))
            duration = int(effect.params.get("duration", 2))
            status_name = effect.params.get("status", "dot")
            dot_mult = 1.0 + sum(status.data.get("dot_damage_mult", 0.0) for status in ctx.caster.statuses)
            amount = ctx.damage_dealt * pct * dot_mult
            for target in targets:
                if not target or not target.is_alive:
                    continue
                status = Status(
                    name=status_name,
                    duration=duration,
                    stacks=1,
                    tags=["dot", "debuff"],
                    data={"dot_damage": amount},
                    hooks={
                        "on_turn_end": [{
                            "priority": 10,
                            "timing": "normal",
                            "type": "damage",
                            "amount": "data.dot_damage",
                            "target": "owner",
                            "no_crit": True,
                            "damage_type": "dot"
                        }]
                    },
                    source_name=ctx.caster.name,
                    source_skill=ctx.metadata.get("source_skill")
                )
                target.statuses.append(status)
                print(f"    {hero_tag(target)} gained DoT ({duration} rounds, {amount:.0f} damage/turn).")

        self.handlers["apply_dot_percent_damage_dealt"] = h_apply_dot_percent_damage_dealt

        # Compatibility handlers for older content.
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
                tags=["buff", "cc_immunity"],
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
            targets = self.battle.target_resolver.resolve(self.battle, ctx.caster, effect.params.get("target", "event_target"), ctx)
            if not targets:
                return
            target = targets[0]
            cc_type = ctx.metadata.get("cc_type")
            if cc_type:
                target.statuses = [status for status in target.statuses if status.name != cc_type]
            heal_mult = float(effect.params.get("heal_mult", 1.5))
            heal_effect = Effect("heal", mult=heal_mult, target="event_target")
            self.execute_effect(heal_effect, EffectContext(self.battle, ctx.caster, [target], ctx.event, ctx.round, ctx.metadata))
            print(f"    {hero_tag(ctx.caster)} dispelled {cc_type} from {hero_tag(target)}.")

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

        self.register("damage", h_damage)
        self.register("heal", h_heal)
        self.register("apply_status", h_apply_status)
        self.register("remove_status", h_remove_status)
        self.register("add_stack", h_add_stack)
        self.register("set_stack", h_set_stack)
        self.register("consume_stack", h_consume_stack)
        self.register("modify_stat", h_modify_stat)
        self.register("sequence", h_sequence)
        self.register("conditional", h_conditional)
        self.register("repeat", h_repeat)
        self.register("heal_max_hp_pct", h_heal_max_hp_pct)
        self.register("random_choice", h_random_choice)
        self.register("trigger_event", h_trigger_event)
        self.register("listen_event", h_listen_event)
        self.register("modify_behavior", h_modify_behavior)
        self.register("apply_dot", h_apply_dot)

        self.register("apply_cc", h_apply_cc)
        self.register("apply_cc_immunity", h_apply_cc_immunity)
        self.register("modify_heal", h_modify_heal)
        self.register("override_basic", h_override_basic)
        self.register("angela_dispel", h_angela_dispel)
        self.register("apply_shield_resonance", h_apply_shield_resonance)
