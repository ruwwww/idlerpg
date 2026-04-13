def hero_tag(hero) -> str:
    color = "\033[92m" if getattr(hero.team, "number", 1) == 1 else "\033[91m"
    reset = "\033[0m"
    return f"{color}[{hero.name}]{reset}"
