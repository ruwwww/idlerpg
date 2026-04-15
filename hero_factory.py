from __future__ import annotations

from typing import Callable, List, Set

from content_loader import HeroContentSource


class HeroRuntimeFactory:
    def __init__(
        self,
        source: HeroContentSource,
        hero_ctor: Callable,
        skill_ctor: Callable,
        passive_ctor: Callable,
        effect_ctor: Callable,
        default_level: int = 160,
        reference_level: int = 160,
        growth_exponent: float = 1.0,
    ):
        self.source = source
        self.hero_ctor = hero_ctor
        self.skill_ctor = skill_ctor
        self.passive_ctor = passive_ctor
        self.effect_ctor = effect_ctor
        self.default_level = default_level      # used when hero JSON has no "level" field
        self.reference_level = reference_level  # level at which JSON stats are defined
        self.growth_exponent = growth_exponent  # 1.0 = linear, >1 = accelerating curve

    def _apply_artifact_stats(self, hero, artifacts: List):
        hp_flat = 0.0
        hp_mult = 0.0
        atk_flat = 0.0
        atk_mult = 0.0
        defense_flat = 0.0
        defense_mult = 0.0
        speed_flat = 0.0
        crit_chance_add = 0.0
        crit_damage_add = 0.0
        precision_add = 0.0
        block_add = 0.0

        for artifact in artifacts:
            bonuses = artifact.stat_bonuses
            hp_flat += float(bonuses.get("max_hp_flat", 0.0)) + float(bonuses.get("hp_flat", 0.0))
            hp_mult += float(bonuses.get("max_hp_mult", 0.0)) + float(bonuses.get("hp_mult", 0.0))
            atk_flat += float(bonuses.get("atk_flat", 0.0))
            atk_mult += float(bonuses.get("atk_mult", 0.0))
            defense_flat += float(bonuses.get("defense_flat", 0.0))
            defense_mult += float(bonuses.get("defense_mult", 0.0))
            speed_flat += float(bonuses.get("speed_flat", 0.0))
            crit_chance_add += float(bonuses.get("crit_chance_add", 0.0))
            crit_damage_add += float(bonuses.get("crit_damage_add", 0.0))
            precision_add += float(bonuses.get("precision_add", 0.0))
            block_add += float(bonuses.get("block_add", 0.0))

        if hp_flat != 0.0 or hp_mult != 0.0:
            hero.max_hp = max(1.0, hero.max_hp * max(0.0, 1.0 + hp_mult) + hp_flat)
            hero.hp = hero.max_hp
            hero.max_shield = hero.max_hp

        if atk_flat != 0.0 or atk_mult != 0.0:
            hero.atk = max(1.0, hero.atk * max(0.0, 1.0 + atk_mult) + atk_flat)

        if defense_flat != 0.0 or defense_mult != 0.0:
            hero.defense = max(0.0, hero.defense * max(0.0, 1.0 + defense_mult) + defense_flat)

        if speed_flat != 0.0:
            hero.speed = max(1, int(round(hero.speed + speed_flat)))

        hero.crit_chance = max(0.0, hero.crit_chance + crit_chance_add)
        hero.crit_damage = max(1.0, hero.crit_damage + crit_damage_add)
        hero.precision = max(0.0, hero.precision + precision_add)
        hero.block = max(0.0, hero.block + block_add)

    def _build_artifact_passives(self, artifacts: List):
        out = []
        seen_unique_keys: Set[str] = set()
        for artifact in artifacts:
            passive_def = artifact.passive
            if not passive_def:
                continue
            unique_key = passive_def.unique_key or artifact.id
            if unique_key in seen_unique_keys:
                continue
            seen_unique_keys.add(unique_key)
            out.append(
                self.passive_ctor(
                    passive_def.name,
                    passive_def.trigger_event,
                    [
                        self.effect_ctor(effect.type, **effect.params)
                        for effect in passive_def.effects
                    ],
                )
            )
        return out

    def _scale_stat(self, base: float, hero_level: int) -> float:
        """Scale a stat from reference_level to hero_level.

        At hero_level == reference_level the scale factor is exactly 1.0,
        so every existing hero JSON stays unchanged by default.
        Speed is intentionally NOT scaled here — it is a kit stat.
        """
        if hero_level == self.reference_level:
            return base
        scale = (hero_level / self.reference_level) ** self.growth_exponent
        return base * scale

    def create_hero(self, hero_id: str, artifact_ids_override: List[str] | None = None):
        hero_def = self.source.get_hero(hero_id)
        hero_level = hero_def.level if hero_def.level is not None else self.default_level

        hero = self.hero_ctor(
            hero_def.name,
            hero_def.speed,                              # Speed: not scaled (kit stat)
            self._scale_stat(hero_def.atk, hero_level),
            self._scale_stat(hero_def.hp, hero_level),
            self._scale_stat(hero_def.defense, hero_level),
            hero_level,
        )
        hero.precision = hero_def.precision
        hero.block = hero_def.block

        selected_artifact_ids = hero_def.artifact_ids if artifact_ids_override is None else artifact_ids_override
        artifact_defs = [self.source.get_artifact(artifact_id) for artifact_id in selected_artifact_ids]
        hero.artifacts = [artifact.id for artifact in artifact_defs]
        self._apply_artifact_stats(hero, artifact_defs)

        if hero_def.basic_skill_id:
            basic_def = self.source.get_skill(hero_def.basic_skill_id)
            hero.basic_skill = self.skill_ctor(
                basic_def.name,
                [
                    self.effect_ctor(effect.type, **effect.params)
                    for effect in basic_def.effects
                ],
            )

        if hero_def.active_skill_id:
            skill_def = self.source.get_skill(hero_def.active_skill_id)
            hero.active_skill = self.skill_ctor(
                skill_def.name,
                [
                    self.effect_ctor(effect.type, **effect.params)
                    for effect in skill_def.effects
                ],
            )

        hero.passives = [
            self.passive_ctor(
                passive_def.name,
                passive_def.trigger_event,
                [
                    self.effect_ctor(effect.type, **effect.params)
                    for effect in passive_def.effects
                ],
            )
            for passive_def in (self.source.get_passive(pid) for pid in hero_def.passive_ids)
        ]
        hero.passives.extend(self._build_artifact_passives(artifact_defs))

        return hero

    def create_team_heroes(self, team_id: str) -> List:
        return [
            self.create_hero(member.hero_id, member.artifact_ids)
            for member in self.source.get_team_members(team_id)
        ]
