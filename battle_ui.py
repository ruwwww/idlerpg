from __future__ import annotations

from typing import List


RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"


def _bar(current: float, maximum: float, width: int = 18, fill: str = "#", empty: str = "-") -> str:
    if maximum <= 0:
        maximum = 1
    ratio = max(0.0, min(1.0, current / maximum))
    filled = int(round(ratio * width))
    return f"[{fill * filled}{empty * (width - filled)}]"


def _fmt_pct(current: float, maximum: float) -> str:
    if maximum <= 0:
        return "0%"
    return f"{int(max(0.0, min(100.0, (current / maximum) * 100.0)))}%"


def _effect_tags(hero, current_round: int) -> List[str]:
    tags: List[str] = []

    # Active CC state map comes from combat logic.
    for cc_name, until_round in sorted(hero.cc_states.items()):
        if until_round > current_round:
            code = cc_name.replace("_", " ").upper()
            short = "".join(word[0] for word in code.split())[:3]
            tags.append(short or "CC")

    for buff in hero.buffs:
        base = "DBF" if buff.is_debuff else "BUF"
        tags.append(f"{base}:{buff.name[:6].upper()}")

    if hero.basic_attack_override is not None:
        tags.append("OVR")
    if hero.energy >= 100:
        tags.append("ULT")
    if not hero.flags.get("passives_enabled", True):
        tags.append("P-OFF")
    if not hero.is_alive:
        tags.append("KO")

    return tags[:6]


def render_hero_line(hero, current_round: int) -> str:
    hp_bar = _bar(hero.hp, hero.max_hp, width=20)
    en_bar = _bar(hero.energy, 100.0, width=12, fill="=", empty=".")
    hp_pct = _fmt_pct(hero.hp, hero.max_hp)
    en_pct = _fmt_pct(min(hero.energy, 100.0), 100.0)

    name_color = CYAN if hero.is_alive else DIM
    hp_color = GREEN if hero.is_alive else DIM
    en_color = YELLOW if hero.energy >= 100 else BLUE

    tags = _effect_tags(hero, current_round)
    tag_text = " ".join(f"[{t}]" for t in tags) if tags else "[NONE]"
    if not hero.is_alive:
        tag_text = f"{DIM}{tag_text}{RESET}"

    return (
        f"{name_color}{BOLD}{hero.name:<14}{RESET} "
        f"HP {hp_color}{hp_bar}{RESET} {hp_pct:>4} | "
        f"EN {en_color}{en_bar}{RESET} {en_pct:>4} | "
        f"FX {MAGENTA}{tag_text}{RESET}"
    )


def render_team_block(team, current_round: int) -> str:
    lines = [f"{BOLD}TEAM {team.number}{RESET}"]
    for hero in team.heroes:
        lines.append(render_hero_line(hero, current_round))
    return "\n".join(lines)


def render_battle_ui(team1, team2, current_round: int):
    divider = f"{DIM}{'-' * 110}{RESET}"
    print(divider)
    print(f"{BOLD}{RED}ROUND {current_round + 1}{RESET}")
    print(render_team_block(team1, current_round))
    print(divider)
    print(render_team_block(team2, current_round))
    print(divider)
