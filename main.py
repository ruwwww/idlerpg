from pathlib import Path

from content_loader import JsonHeroContentSource
from engine.battle import BattleEngine
from engine.models import Effect, Hero, Passive, Skill, Team
from hero_factory import HeroRuntimeFactory


def build_default_setup() -> tuple[Team, Team, dict]:
    content_path = Path(__file__).parent / "data" / "content"
    source = JsonHeroContentSource(str(content_path))

    probe_engine = BattleEngine(Team([], 1), Team([], 2))
    source.validate_effect_types(set(probe_engine.executor.handlers.keys()))

    runtime_factory = HeroRuntimeFactory(source, Hero, Skill, Passive, Effect)
    team1 = Team(runtime_factory.create_team_heroes("artifact_loadout_demo"), 1)
    team2 = Team(runtime_factory.create_team_heroes("team2_default"), 2)
    team1.opposite = team2
    team2.opposite = team1
    return team1, team2, source.get_battle_config()


def build_default_teams() -> tuple[Team, Team]:
    team1, team2, _ = build_default_setup()
    return team1, team2


if __name__ == "__main__":
    team1, team2, battle_config = build_default_setup()
    engine = BattleEngine(team1, team2, battle_config=battle_config)
    engine.simulate(max_rounds=999)
