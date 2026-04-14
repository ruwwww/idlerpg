from __future__ import annotations

from typing import Callable, List

from content_loader import HeroContentSource


class HeroRuntimeFactory:
    def __init__(
        self,
        source: HeroContentSource,
        hero_ctor: Callable,
        skill_ctor: Callable,
        passive_ctor: Callable,
        effect_ctor: Callable,
    ):
        self.source = source
        self.hero_ctor = hero_ctor
        self.skill_ctor = skill_ctor
        self.passive_ctor = passive_ctor
        self.effect_ctor = effect_ctor

    def create_hero(self, hero_id: str):
        hero_def = self.source.get_hero(hero_id)
        hero = self.hero_ctor(
            hero_def.name,
            hero_def.speed,
            hero_def.atk,
            hero_def.hp,
            hero_def.defense,
        )

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

        return hero

    def create_team_heroes(self, team_id: str) -> List:
        return [self.create_hero(hero_id) for hero_id in self.source.get_team_hero_ids(team_id)]
