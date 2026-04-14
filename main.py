from pathlib import Path

from content_loader import JsonHeroContentSource
from engine.battle import BattleEngine
from engine.models import Effect, Hero, Passive, Skill, Team
from hero_factory import HeroRuntimeFactory


def build_default_teams() -> tuple[Team, Team]:
    content_path = Path(__file__).parent / "data" / "content"
    source = JsonHeroContentSource(str(content_path))

    probe_engine = BattleEngine(Team([], 1), Team([], 2))
    source.validate_effect_types(set(probe_engine.executor.handlers.keys()))

    runtime_factory = HeroRuntimeFactory(source, Hero, Skill, Passive, Effect)
    team1 = Team(runtime_factory.create_team_heroes("team1_default"), 1)
    team2 = Team(runtime_factory.create_team_heroes("team1_rexus_demo"), 2)
    team1.opposite = team2
    team2.opposite = team1
    return team1, team2


if __name__ == "__main__":
    team1, team2 = build_default_teams()
    engine = BattleEngine(team1, team2)
    engine.simulate(max_rounds=30)
