# ================================================
# IDLE HEROES-STYLE COMBAT SIMULATOR (Modular & Future-Proof)
# Complete, runnable Python code.
# Copy-paste into a file (e.g. idle_combat.py) and run.
#
# Features demonstrated:
#   • Speed-ordered turns
#   • Energy system + over-energy bonus
#   • Multi-effect skills & passives
#   • Tara-style passive disable (Seal of Light)
#   • Basic attack override (target allies + convert to damage)
#   • Healing → damage conversion
#   • Event system + modifiers + flags
#   • Data-driven effects (easy to extend)
#   • Full fight logger for debugging
#
# How to extend for "retarded" mechanics:
#   1. Add a new Effect.type
#   2. Add a handler in effect_handlers
#   3. Register listeners on events if needed
#   4. Load from JSON later (see comment at bottom)
# ================================================

import random
from collections import defaultdict
from typing import Callable, Any, Dict, List

# ====================== EVENT SYSTEM ======================
class EventSystem:
    def __init__(self):
        self.listeners: Dict[str, List[Callable]] = defaultdict(list)

    def on(self, event_name: str, callback: Callable):
        self.listeners[event_name].append(callback)

    def emit(self, event_name: str, **payload):
        for callback in self.listeners[event_name]:
            callback(**payload)


# ====================== CORE CLASSES ======================
class Buff:
    def __init__(self, name: str, value: float, duration_rounds: int, max_stacks: int = 1, is_debuff: bool = False):
        self.name = name
        self.value = value
        self.duration = duration_rounds
        self.stacks = 1
        self.max_stacks = max_stacks
        self.is_debuff = is_debuff


class Effect:
    """Atomic, reusable building block for ANY skill or passive effect."""
    def __init__(self, type_: str, **params):
        self.type = type_          # e.g. "damage", "apply_cc", "override_basic", "modify_heal"
        self.params = params       # flexible dict for all parameters


class Skill:
    def __init__(self, name: str, effects: List[Effect]):
        self.name = name
        self.effects = effects


class Passive:
    def __init__(self, name: str, trigger_event: str, effects: List[Effect]):
        self.name = name
        self.trigger_event = trigger_event   # e.g. "on_basic_hit", "after_skill", "on_death"
        self.effects = effects


class Modifier:
    """Wraps calculations (damage, heal, targeting, etc.)."""
    def __init__(self, name: str, func: Callable, priority: int = 0):
        self.name = name
        self.func = func          # lambda or function that modifies value
        self.priority = priority


class Hero:
    def __init__(self, name: str, speed: int, atk: float, hp: float, defense: float):
        self.name = name
        self.speed = speed
        self.atk = atk
        self.hp = hp
        self.max_hp = hp
        self.defense = defense
        self.energy = 0.0
        self.is_alive = True
        self.team = None                     # will be set to 0 or 1

        # Modular bags
        self.buffs: List[Buff] = []
        self.passives: List[Passive] = []
        self.active_skill: Skill | None = None

        self.flags: Dict[str, Any] = {"passives_enabled": True, "sealed_until": -1}
        self.modifiers: Dict[str, List[Modifier]] = defaultdict(list)   # "damage", "heal", "basic_target"
        self.basic_attack_override: Effect | None = None
        self.stacks: Dict[str, int] = {}

        self.event_system = EventSystem()    # per-hero listeners (for cross-hero effects)

    def compute_final_atk(self) -> float:
        """Simple final stat (expand with % bonuses later)."""
        return self.atk * (1 + sum(b.value for b in self.buffs if b.name == "atk_buff"))

    def is_cc_blocked(self) -> bool:
        return not self.flags["passives_enabled"]   # can expand with more CC


# ====================== EFFECT HANDLERS (the heart of scalability) ======================
effect_handlers: Dict[str, Callable] = {}

def register_effect_handler(type_name: str, func: Callable):
    effect_handlers[type_name] = func

# --- Example handlers (add new ones here for crazy mechanics) ---
def _handle_damage(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    for target in targets:
        dmg = caster.compute_final_atk() * effect.params.get("mult", 1.0)
        # Apply all damage modifiers in priority order
        for mod in sorted(caster.modifiers["damage"] + target.modifiers["damage"], key=lambda m: m.priority):
            dmg = mod.func(dmg, target, caster) or dmg
        apply_damage(target, dmg, context.get("is_crit", False))

def _handle_apply_cc(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    cc_type = effect.params["cc_type"]
    duration = effect.params.get("duration", 1)
    for target in targets:
        if cc_type == "seal_of_light":
            target.flags["passives_enabled"] = False
            target.flags["sealed_until"] = context["current_round"] + duration
            # Reset specific stacks (example)
            if "power_of_light" in target.stacks:
                target.stacks["power_of_light"] = 0
        # You can add more CC types easily

def _handle_override_basic(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    caster.basic_attack_override = effect   # store for execute_basic_attack

def _handle_modify_heal(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    # Example: turn heal into damage
    def inverter(amt: float, t: Hero, s: Hero) -> float:
        return -amt
    caster.modifiers["heal"].append(Modifier("heal_inverter", inverter, priority=100))

register_effect_handler("damage", _handle_damage)
register_effect_handler("apply_cc", _handle_apply_cc)
register_effect_handler("override_basic", _handle_override_basic)
register_effect_handler("modify_heal", _handle_modify_heal)


# ====================== CORE COMBAT FUNCTIONS ======================
def apply_damage(target: Hero, amount: float, is_crit: bool = False):
    if not target.is_alive:
        return
    target.hp -= max(0, amount)
    if target.hp <= 0:
        target.is_alive = False
        target.event_system.emit("on_death", target=target)

    # Idle Heroes energy gain on being hit
    gain = 20 if is_crit else 10
    target.energy = min(target.energy + gain, 999)

def apply_heal(target: Hero, amount: float, source: Hero):
    for mod in sorted(target.modifiers["heal"] + source.modifiers["heal"], key=lambda m: m.priority):
        amount = mod.func(amount, target, source) or amount
    if amount < 0:                     # healing inverted to damage
        apply_damage(target, -amount)
    else:
        target.hp = min(target.max_hp, target.hp + amount)

def execute_basic_attack(caster: Hero):
    if caster.basic_attack_override:
        eff = caster.basic_attack_override
        # Example override: target allies + convert to damage
        targets = [h for h in caster.team.heroes if h.is_alive] if eff.params.get("target_allies") else get_enemies(caster)
        for t in targets:
            if eff.params.get("convert_to_damage", False):
                dmg = caster.compute_final_atk() * eff.params.get("mult", 1.0)
                apply_damage(t, dmg)
            else:
                # normal heal or whatever
                pass
        caster.basic_attack_override = None  # one-time use unless you want persistent
    else:
        # Normal single-target enemy
        targets = [pick_target(caster)]
        _handle_damage(Effect("damage", mult=1.0), caster, targets, {})

    # Trigger basic-attack events
    caster.event_system.emit("on_basic_hit", caster=caster)
    
    # Gain energy for attacking
    caster.energy = min(caster.energy + 50, 999)

def execute_skill(caster: Hero, skill: Skill, overcharge_bonus: float = 0.0):
    for effect in skill.effects:
        targets = get_targets_for_effect(effect, caster)   # you can expand this
        context = {"current_round": global_round, "overcharge": overcharge_bonus}
        if effect.type in effect_handlers:
            effect_handlers[effect.type](effect, caster, targets, context)
    caster.event_system.emit("after_skill", caster=caster)

def trigger_passives(hero: Hero, event_name: str, **context):
    if not hero.flags["passives_enabled"]:
        return
    for passive in hero.passives:
        if passive.trigger_event == event_name:
            for effect in passive.effects:
                targets = get_targets_for_effect(effect, hero)
                if effect.type in effect_handlers:
                    effect_handlers[effect.type](effect, hero, targets, context)


# ====================== HELPERS ======================
def get_enemies(caster: Hero):
    return [h for h in caster.team.opposite.heroes if h.is_alive]

def pick_target(caster: Hero):
    enemies = get_enemies(caster)
    return min(enemies, key=lambda h: h.hp) if enemies else None

def get_targets_for_effect(effect: Effect, caster: Hero) -> List[Hero]:
    # Expand this for "all_enemies", "self", "random", etc.
    if effect.params.get("target_all_enemies"):
        return get_enemies(caster)
    return [caster] if effect.params.get("target_self") else [pick_target(caster)] or []

global_round = 0

def process_round_end(all_heroes: List[Hero]):
    global global_round
    global_round += 1
    for hero in all_heroes:
        if not hero.is_alive:
            continue
        # Tick buffs
        for b in hero.buffs[:]:
            b.duration -= 1
            if b.duration <= 0:
                hero.buffs.remove(b)
        # Unseal if time is up
        if hero.flags.get("sealed_until", -1) == global_round:
            hero.flags["passives_enabled"] = True


# ====================== TEAM & SIMULATION ======================
class Team:
    def __init__(self, heroes: List[Hero], number: int):
        self.heroes = heroes
        self.number = number
        for h in heroes:
            h.team = self
        self.opposite = None   # set after both teams created

def simulate_fight(team1: Team, team2: Team, max_rounds: int = 50):
    global global_round
    global_round = 0
    all_heroes = team1.heroes + team2.heroes
    team1.opposite = team2
    team2.opposite = team1

    print("=== FIGHT START ===\n")
    while any(h.is_alive for h in team1.heroes) and any(h.is_alive for h in team2.heroes) and global_round < max_rounds:
        # Sort by speed descending, ties broken by team slot order
        acting_order = sorted(all_heroes, key=lambda h: (-h.speed, all_heroes.index(h)))
        
        for hero in acting_order:
            if not hero.is_alive:
                continue
            final_atk = hero.compute_final_atk()

            print(f"Round {global_round+1} | {hero.name} (Energy: {hero.energy:.0f}) → ", end="")

            if hero.energy >= 100:
                over = hero.energy - 100
                bonus = over * 0.01   # +1% skill damage per excess energy
                print(f"SKILL {hero.active_skill.name} (bonus {bonus*100:.0f}%)")
                execute_skill(hero, hero.active_skill, bonus)
                hero.energy = 0
            else:
                print("BASIC ATTACK")
                execute_basic_attack(hero)

            # Trigger passives for this action
            trigger_passives(hero, "after_action")

        process_round_end(all_heroes)

    print("\n=== FIGHT END ===")
    for t in [team1, team2]:
        alive = sum(1 for h in t.heroes if h.is_alive)
        print(f"Team {t.number}: {alive}/5 alive")


# ====================== HERO FACTORIES (data-driven style) ======================
def create_mareia_like() -> Hero:
    h = Hero("Mareia", speed=1250, atk=52000, hp=280000, defense=8500)
    h.active_skill = Skill("Crashing Tide", [
        Effect("damage", mult=1.3, target_all_enemies=True),
        Effect("damage", mult=0.8, target_all_enemies=True),   # extra conditional in real game
        Effect("apply_cc", cc_type="freeze", duration=1)
    ])
    h.passives = [
        Passive("Freeze on Hit", "on_basic_hit", [Effect("apply_cc", cc_type="freeze", duration=1)])
    ]
    return h


def create_tara_like() -> Hero:
    h = Hero("Tara", speed=1180, atk=48000, hp=320000, defense=12000)
    h.active_skill = Skill("Seal of Light", [
        Effect("apply_cc", cc_type="seal_of_light", duration=3),
        Effect("damage", mult=1.2, target_all_enemies=True)
    ])
    # Passive that would be disabled by her own seal if she were enemy
    h.passives = [Passive("Might of Light", "after_skill", [Effect("damage", mult=0.5)])]
    return h


def create_heal_inverter_support() -> Hero:
    h = Hero("Inverter", speed=1100, atk=30000, hp=250000, defense=6000)
    h.active_skill = Skill("Inverted Blessing", [
        Effect("modify_heal"),   # registers inverter
        Effect("damage", mult=0.9, target_all_enemies=True)
    ])
    # Passive that forces basic attack to hit allies as damage
    h.passives = [
        Passive("Twisted Basic", "on_basic_hit", [
            Effect("override_basic", target_allies=True, convert_to_damage=True)
        ])
    ]
    return h


# ====================== RUN EXAMPLE ======================
if __name__ == "__main__":
    # Team 1 - normal + complex heroes
    team1_heroes = [
        create_mareia_like(),
        create_tara_like(),
        Hero("Basic Tank", 900, 40000, 400000, 15000),
        create_heal_inverter_support(),
        Hero("DPS", 1300, 65000, 180000, 5000)
    ]
    team1 = Team(team1_heroes, 1)

    # Team 2 - simple enemies
    team2_heroes = [
        Hero("Enemy1", 950, 45000, 300000, 9000),
        Hero("Enemy2", 1100, 55000, 250000, 7000),
        Hero("Enemy3", 1000, 48000, 280000, 8000),
        Hero("Enemy4", 1200, 52000, 220000, 6000),
        Hero("Enemy5", 1050, 50000, 260000, 7500)
    ]
    team2 = Team(team2_heroes, 2)

    simulate_fight(team1, team2, max_rounds=30)

    # How to make it fully data-driven later:
    # 1. Save heroes as JSON with "active_skill": {"effects": [...]}
    # 2. Write a loader that creates Effect objects from dicts
    # 3. Add new Effect types + handlers without touching the loop