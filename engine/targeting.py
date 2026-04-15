from __future__ import annotations

import random
from typing import Any, List

from .models import EffectContext, Hero


class TargetResolver:
    def _get_taunt_forced_target(self, caster: Hero, enemies: List[Hero]) -> Hero | None:
        force_status = next((status for status in caster.statuses if status.data.get("force_target_source")), None)
        if not force_status or not force_status.source_name:
            return None
        for enemy in enemies:
            if enemy.name == force_status.source_name:
                return enemy
        return None

    def _mark_taunt_forced(self, ctx: EffectContext, target: Hero) -> None:
        ctx.metadata["taunt_forced"] = True
        ctx.metadata["taunt_forced_target"] = target.name

    def resolve(self, battle, caster: Hero, target_def: Any, ctx: EffectContext) -> List[Hero]:
        if target_def is None:
            default = battle.pick_default_target(caster)
            return [default] if default else []

        if isinstance(target_def, str):
            target_def = {"selector": target_def}

        selector = target_def.get("selector")
        n = int(target_def.get("n", 1))

        enemies = [h for h in caster.team.opposite.heroes if h.is_alive]
        allies = [h for h in caster.team.heroes if h.is_alive]
        taunt_target = self._get_taunt_forced_target(caster, enemies)

        if selector == "event_target":
            meta_target = ctx.metadata.get("event_target")
            if meta_target and getattr(meta_target, "is_alive", False):
                return [meta_target]
            if ctx.targets:
                return [t for t in ctx.targets if t and t.is_alive]
            return []

        if selector == "event_source":
            source = ctx.metadata.get("event_source") or ctx.metadata.get("source")
            if source and getattr(source, "is_alive", False):
                return [source]
            return []

        if selector == "self":
            return [caster]
        if selector == "all_enemies":
            return enemies
        if selector == "all_allies":
            return allies
        if selector == "all_other_allies":
            return [h for h in allies if h != caster]
        if selector == "random_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            return random.sample(enemies, min(n, len(enemies))) if enemies else []
        if selector == "random_other_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            excluded = set(ctx.targets or [])
            event_target = ctx.metadata.get("event_target")
            if event_target:
                excluded.add(event_target)
            pool = [h for h in enemies if h not in excluded]
            return random.sample(pool, min(n, len(pool))) if pool else []
        if selector == "random_allies":
            pool = [h for h in allies if h != caster] or allies
            return random.sample(pool, min(n, len(pool))) if pool else []

        if selector == "owner":
            owner = ctx.metadata.get("owner")
            if owner and getattr(owner, "is_alive", False):
                return [owner]
            return []

        if selector == "lowest_hp_enemy":
            if taunt_target:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            return [min(enemies, key=lambda h: h.hp / max(1, h.max_hp))] if enemies else []
        if selector == "highest_crit_chance_enemy":
            if taunt_target:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            return [max(enemies, key=lambda h: h.crit_chance)] if enemies else []
        if selector == "highest_atk_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            sorted_enemies = sorted(enemies, key=lambda h: h.compute_final_atk(), reverse=True)
            return sorted_enemies[:n]
        if selector == "highest_hp_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            sorted_enemies = sorted(enemies, key=lambda h: h.hp, reverse=True)
            return sorted_enemies[:n]
        if selector == "lowest_hp_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            sorted_enemies = sorted(enemies, key=lambda h: h.hp)
            return sorted_enemies[:n]
        if selector == "lowest_hp_pct_allies":
            other_allies = [h for h in allies if h != caster]
            sorted_allies = sorted(other_allies, key=lambda h: h.hp / max(1, h.max_hp))
            return sorted_allies[:n]
        if selector == "highest_atk_allies":
            other_allies = [h for h in allies if h != caster]
            sorted_allies = sorted(other_allies, key=lambda h: h.compute_final_atk(), reverse=True)
            return sorted_allies[:n]
        if selector == "lowest_hp_pct_allies_priority":
            other_allies = [h for h in allies if h != caster]
            if other_allies:
                return [min(other_allies, key=lambda h: h.hp / max(1, h.max_hp))]
            return [caster]
        if selector == "lowest_hp_allies_priority":
            other_allies = [h for h in allies if h != caster]
            if other_allies:
                return [min(other_allies, key=lambda h: h.hp)]
            return [caster]
        if selector == "highest_atk_allies_priority":
            other_allies = [h for h in allies if h != caster]
            pool = other_allies or allies
            if pool:
                return [max(pool, key=lambda h: h.compute_final_atk())]
            return []
        if selector == "second_lowest_hp_pct_allies":
            other_allies = [h for h in allies if h != caster]
            if len(other_allies) < 2:
                return []
            sorted_allies = sorted(other_allies, key=lambda h: h.hp / max(1, h.max_hp))
            return [sorted_allies[1]]
        if selector == "highest_atk_enemies":
            return sorted(enemies, key=lambda h: h.atk, reverse=True)[:n]
        if selector == "random_top_atk_enemies":
            if taunt_target and n <= 1:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            top_n = int(target_def.get("top_n", 3))
            top = sorted(enemies, key=lambda h: h.atk, reverse=True)[:top_n]
            return random.sample(top, min(n, len(top))) if top else []
        if selector == "marked_target":
            if taunt_target:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            mark = target_def.get("status", "abyssal_eyes")
            for enemy in enemies:
                if enemy.get_status(mark):
                    return [enemy]
            return []
        if selector == "marked_plus_random_enemy":
            mark = target_def.get("status", "abyssal_eyes")
            marked = None
            for enemy in enemies:
                if enemy.get_status(mark):
                    marked = enemy
                    break
            remaining = [enemy for enemy in enemies if enemy != marked]
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
            for enemy in enemies:
                if enemy.get_status(mark):
                    marked = enemy
                    break
            remaining = [enemy for enemy in enemies if enemy != marked]
            top_others = sorted(remaining, key=lambda h: h.atk, reverse=True)[:n]
            out = []
            if marked:
                out.append(marked)
            out.extend(top_others)
            return out

        if selector == "random_marked_enemy_priority":
            # Picks randomly from marked enemies (preferred); falls back to random enemy.
            # target_def["status"] names the mark to look for (default "vulnerability").
            if taunt_target:
                self._mark_taunt_forced(ctx, taunt_target)
                return [taunt_target]
            mark = target_def.get("status", "vulnerability")
            marked = [e for e in enemies if e.get_status(mark)]
            if marked:
                return [random.choice(marked)]
            return [random.choice(enemies)] if enemies else []

        default = battle.pick_default_target(caster)
        return [default] if default else []
