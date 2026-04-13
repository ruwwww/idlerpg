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
from pathlib import Path
from typing import Callable, Any, Dict, List

from battle_ui import render_battle_ui
from content_loader import JsonHeroContentSource
from hero_factory import HeroRuntimeFactory


def _hero_tag(hero: "Hero") -> str:
    color = "\033[92m" if getattr(hero.team, "number", 1) == 1 else "\033[91m"
    reset = "\033[0m"
    return f"{color}[{hero.name}]{reset}"


def _fmt_target_list(targets: List["Hero"]) -> str:
    clean = [t for t in targets if t is not None]
    if not clean:
        return "no one"
    tags = [_hero_tag(t) for t in clean]
    if len(tags) == 1:
        return tags[0]
    if len(tags) == 2:
        return f"{tags[0]} and {tags[1]}"
    return ", ".join(tags[:-1]) + f", and {tags[-1]}"


def _log_action(caster: "Hero", action: str, targets: List["Hero"], detail: str = ""):
    target_text = _fmt_target_list(targets)
    if action == "SKILL":
        msg = f"    {_hero_tag(caster)} ({caster.hp:.0f}) cast [{detail}] targeting {target_text}."
    elif action == "BASIC":
        msg = f"    {_hero_tag(caster)} ({caster.hp:.0f}) attacked {target_text}."
    elif action == "BASIC_OVERRIDE":
        msg = f"    {_hero_tag(caster)} triggered a modified basic attack on {target_text}."
    else:
        msg = f"    {_hero_tag(caster)} acts on {target_text}."
    print(msg)


def _log_effect(caster: "Hero", effect: "Effect", targets: List["Hero"]):
    target_text = _fmt_target_list(targets)
    if effect.type == "damage":
        mult = effect.params.get("mult", 1.0)
        print(f"  The damage effect ({mult:.2f}x) from {_hero_tag(caster)} is resolved against {target_text}.")
    elif effect.type == "apply_cc":
        cc_type = effect.params.get("cc_type", "unknown")
        duration = effect.params.get("duration", 1)
        print(f"  {_hero_tag(caster)} attempts to inflict {cc_type} on {target_text} for {duration} turn(s).")
    elif effect.type == "modify_heal":
        print(f"  {_hero_tag(caster)} altered healing behavior for this battle.")
    elif effect.type == "override_basic":
        print(f"  {_hero_tag(caster)} changed the next basic attack behavior.")
    # We can omit printing generic event types so we don't spam unformatted params
    elif effect.type not in ["apply_cc_immunity", "angela_dispel", "apply_shield_resonance"]: 
        print(f"  {_hero_tag(caster)} triggered {effect.type} on {target_text}.")

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
        self.shield = 0.0
        self.max_shield = hp
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
        self.cc_states: Dict[str, int] = {}

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
    total_shield_gained = 0
    shield_steal_pct = effect.params.get("shield_steal_pct", 0.0)
    for target in targets:
        if target is None or not target.is_alive:
            continue
        dmg = caster.compute_final_atk() * effect.params.get("mult", 1.0)
        # Apply all damage modifiers in priority order
        for mod in sorted(caster.modifiers["damage"] + target.modifiers["damage"], key=lambda m: m.priority):
            dmg = mod.func(dmg, target, caster) or dmg
        # Taunt damage reduction
        if "taunt" in target.cc_states and target.cc_states["taunt"]["taunter"] == caster.name:
            reduction = target.cc_states["taunt"]["damage_reduction_pct"] / 100.0
            dmg *= (1 - reduction)
            print(f"    Taunt reduced damage by {reduction*100:.0f}%.")
            
        # Target shield passive damage reduction
        if target.shield > 0 and target.flags.get("has_shield_dr_pct"):
            shield_reduction = target.flags["has_shield_dr_pct"] / 100.0
            dmg *= (1 - shield_reduction)
            print(f"    {_hero_tag(target)}'s shield resonance reduced damage by {shield_reduction*100:.0f}%.")

        if context.get("damage_source") == "basic":
            print(f"  {_hero_tag(caster)} ({caster.hp:.0f}) attacked {_hero_tag(target)}, dealing {dmg:.0f} damage.")
        else:
            print(f"    {_hero_tag(caster)}'s effect hit {_hero_tag(target)}, dealing {dmg:.0f} damage.")
        apply_damage(target, dmg, context.get("is_crit", False))
        
        if shield_steal_pct > 0:
            total_shield_gained += dmg * shield_steal_pct

    if total_shield_gained > 0:
        apply_shield(caster, total_shield_gained)

def _handle_apply_cc(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    cc_type = effect.params["cc_type"]
    duration = effect.params.get("duration", 1)
    cc_title = cc_type.replace("_", " ").title()
    for target in targets:
        if target is None or not target.is_alive:
            continue

        if target.shield > 0 and target.flags.get("has_shield_cc_resist_pct"):
            resist_chance = target.flags["has_shield_cc_resist_pct"] / 100.0
            if random.random() < resist_chance:
                print(f"    {_hero_tag(target)}'s shield resonated and resisted {cc_title}!")
                continue

        # Check for CC immunity shield
        immune_buffs = [b for b in target.buffs if b.name == "cc_immunity"]
        if immune_buffs:
            b = immune_buffs[0]
            target.buffs.remove(b)
            print(f"    {_hero_tag(target)} blocked {cc_title} with CC Immunity shield!")
            heal_src = getattr(b, "source_hero", target)
            apply_heal(target, b.value, heal_src)
            continue

        until_round = context.get("current_round", 0) + duration
        if cc_type == "taunt":
            target.cc_states[cc_type] = {
                "until": until_round,
                "taunter": caster.name,
                "damage_reduction_pct": effect.params.get("damage_reduction_pct", 0)
            }
        else:
            target.cc_states[cc_type] = max(target.cc_states.get(cc_type, -1), until_round)
        print(f"    {cc_title} effect was applied to {_hero_tag(target)}, lasts for {duration} turn(s).")
        if cc_type == "seal_of_light":
            target.flags["passives_enabled"] = False
            target.flags["sealed_until"] = until_round
            print(f"    {_hero_tag(target)}'s passives are sealed until turn {until_round + 1}.")
            # Reset specific stacks (example)
            if "power_of_light" in target.stacks:
                target.stacks["power_of_light"] = 0

        # Trigger on_ally_receive_cc for all allies so they can dispel it
        for ally in target.team.heroes:
            if ally.is_alive:
                trigger_context = {
                    "current_round": context.get("current_round", 0),
                    "target": target,
                    "cc_type": cc_type,
                    "dispelled": False
                }
                trigger_passives(ally, "on_ally_receive_cc", trigger_context)
                # If dispelled, break out of checking more allies
                if trigger_context.get("dispelled"):
                    break
        # You can add more CC types easily

def _handle_override_basic(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    caster.basic_attack_override = effect   # store for execute_basic_attack
    print(f"    {_hero_tag(caster)} prepared a basic-attack override.")

def _handle_modify_heal(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    # Example: turn heal into damage
    def inverter(amt: float, t: Hero, s: Hero) -> float:
        return -amt
    caster.modifiers["heal"].append(Modifier("heal_inverter", inverter, priority=100))
    print(f"    Healing inversion is now active around {_hero_tag(caster)}.")


def _handle_modify_stat(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    for target in targets:
        stat_type = effect.params.get("stat_type")
        if stat_type == "max_hp":
            mult = effect.params.get("mult", 1.0)
            target.max_hp *= mult
            target.hp = min(target.hp, target.max_hp)
            print(f"    {_hero_tag(target)}'s max HP increased to {target.max_hp:.0f}.")

def _handle_apply_cc_immunity(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    heal_mult = effect.params.get("heal_mult", 2.0)
    duration = effect.params.get("duration", 2)
    heal_amt = caster.compute_final_atk() * heal_mult
    for target in targets:
        # Buff constructor: name, value, duration...
        b = Buff("cc_immunity", heal_amt, duration)
        # Hack to attach heal source to buff
        b.source_hero = caster
        target.buffs.append(b)
        print(f"    {_hero_tag(caster)} granted CC Immunity to {_hero_tag(target)} for {duration} turn(s).")

def _handle_angela_dispel(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    triggered_round = context.get("current_round", global_round)
    # Check if passive triggered this turn
    if caster.stacks.get("angela_passive_triggered") == triggered_round:
        return
    
    chance = effect.params.get("chance", 0.3)
    if random.random() > chance:
        return
        
    for target in targets:
        cc_type = context.get("cc_type")
        if cc_type and cc_type in target.cc_states:
            del target.cc_states[cc_type]
            cc_title = cc_type.replace("_", " ").title()
            print(f"    {_hero_tag(caster)}'s passive dispelled {cc_title} from {_hero_tag(target)}!")
            
            # trigger heal
            heal_mult = effect.params.get("heal_mult", 1.0)
            heal_amt = caster.compute_final_atk() * heal_mult
            apply_heal(target, heal_amt, caster)
            
            # mark as triggered
            caster.stacks["angela_passive_triggered"] = triggered_round
            
            # update context so _handle_apply_cc knows it got blocked/dispelled
            context["dispelled"] = True

def _handle_apply_shield_resonance(effect: Effect, caster: Hero, targets: List[Hero], context: Dict):
    for target in targets:
        target.flags["has_shield_dr_pct"] = effect.params.get("dr_pct", 5)
        target.flags["has_shield_cc_resist_pct"] = effect.params.get("cc_resist_pct", 10)
        print(f"    {_hero_tag(target)} now has shield resonance active.")

register_effect_handler("damage", _handle_damage)
register_effect_handler("apply_cc", _handle_apply_cc)
register_effect_handler("override_basic", _handle_override_basic)
register_effect_handler("modify_heal", _handle_modify_heal)
register_effect_handler("modify_stat", _handle_modify_stat)
register_effect_handler("apply_cc_immunity", _handle_apply_cc_immunity)
register_effect_handler("angela_dispel", _handle_angela_dispel)
register_effect_handler("apply_shield_resonance", _handle_apply_shield_resonance)


# ====================== CORE COMBAT FUNCTIONS ======================
def apply_shield(target: Hero, amount: float):
    if not target.is_alive:
        return
    old_shield = target.shield
    target.shield = min(target.max_shield, target.shield + amount)
    print(f"    {_hero_tag(target)} gained {target.shield - old_shield:.0f} shield! (Current: {target.shield:.0f}/{target.max_shield:.0f})")

def apply_damage(target: Hero, amount: float, is_crit: bool = False):
    if not target.is_alive:
        return
        
    amount = max(0, amount)
    if amount > 0 and target.shield > 0:
        absorbed = min(target.shield, amount)
        target.shield -= absorbed
        amount -= absorbed
        print(f"    {_hero_tag(target)}'s shield absorbed {absorbed:.0f} damage (Remaining: {target.shield:.0f}).")
        
    if amount > 0:
        target.hp -= amount
        print(f"    {_hero_tag(target)} now has {max(0, target.hp):.0f}/{target.max_hp:.0f} HP.")
        if target.hp <= 0:
            target.is_alive = False
            target.event_system.emit("on_death", target=target)
            print(f"    {_hero_tag(target)} has been defeated.")

    # Idle Heroes energy gain on being hit
    gain = 20 if is_crit else 10
    target.energy = min(target.energy + gain, 999)

def apply_heal(target: Hero, amount: float, source: Hero):
    for mod in sorted(target.modifiers["heal"] + source.modifiers["heal"], key=lambda m: m.priority):
        amount = mod.func(amount, target, source) or amount
    if amount < 0:                     # healing inverted to damage
        print(f"    {_hero_tag(source)}'s heal was inverted and dealt {-amount:.0f} damage to {_hero_tag(target)}.")
        apply_damage(target, -amount)
    else:
        target.hp = min(target.max_hp, target.hp + amount)
        print(f"    {_hero_tag(source)} healed {_hero_tag(target)} for {amount:.0f}; {_hero_tag(target)} now has {target.hp:.0f} HP.")

def execute_basic_attack(caster: Hero):
    if caster.basic_attack_override:
        eff = caster.basic_attack_override
        # Delegate basic targeting to effect engine
        targets = get_targets_for_effect(eff, caster)
        _log_action(caster, "BASIC_OVERRIDE", targets, detail=f"params={eff.params}")
        
        if eff.params.get("convert_to_damage", False) or eff.params.get("is_damage", False):
            _handle_damage(eff, caster, targets, {"damage_source": "basic"})
        else:
            # Just dispatch the effect directly if it has a type mapping
            if eff.params.get("actual_type") in effect_handlers:
                effect_handlers[eff.params["actual_type"]](eff, caster, targets, {"damage_source": "basic"})
                
        if not eff.params.get("persistent", False):
            caster.basic_attack_override = None  # one-time use
    else:
        # Normal single-target enemy
        targets = [pick_target(caster)]
        _handle_damage(Effect("damage", mult=1.0), caster, targets, {"damage_source": "basic"})

    # Trigger basic-attack events
    caster.event_system.emit("on_basic_hit", caster=caster)
    
    # Gain energy for attacking
    caster.energy = min(caster.energy + 50, 999)

def execute_skill(caster: Hero, skill: Skill, overcharge_bonus: float = 0.0):
    _log_action(caster, "SKILL", get_enemies(caster), detail=f"{skill.name}")
    if overcharge_bonus > 0:
        print(f"    Overcharge bonus active: {overcharge_bonus*100:.0f}%.")
    for effect in skill.effects:
        context = {
            "current_round": global_round,
            "overcharge": overcharge_bonus,
            "damage_source": "skill",
            "skill_name": skill.name,
        }
        targets = get_targets_for_effect(effect, caster, context)
        _log_effect(caster, effect, targets)
        if effect.type in effect_handlers:
            effect_handlers[effect.type](effect, caster, targets, context)
    caster.event_system.emit("after_skill", caster=caster)

def trigger_passives(hero: Hero, event_name: str, context: Dict = None):
    if context is None:
        context = {}
    if not hero.flags.get("passives_enabled", True):
        return
    for passive in hero.passives:
        if passive.trigger_event == event_name:
            for effect in passive.effects:
                targets = get_targets_for_effect(effect, hero, context)
                if effect.type in effect_handlers:
                    effect_handlers[effect.type](effect, hero, targets, context)


# ====================== HELPERS ======================
def get_enemies(caster: Hero):
    return [h for h in caster.team.opposite.heroes if h.is_alive]

def pick_target(caster: Hero):
    global all_heroes
    taunt_state = caster.cc_states.get("taunt")
    if taunt_state and taunt_state["until"] > global_round:
        taunter_name = taunt_state["taunter"]
        for h in all_heroes:
            if h.name == taunter_name and h.is_alive:
                return h
    enemies = get_enemies(caster)
    return min(enemies, key=lambda h: h.hp) if enemies else None

def get_targets_for_effect(effect: Effect, caster: Hero, context: Dict = None) -> List[Hero]:
    # Event target fallback
    if context and effect.params.get("use_event_target"):
        return [context.get("target")] if context.get("target") else []
    # Expand this for "all_enemies", "self", "random", etc.
    if effect.params.get("target_all_enemies"):
        return get_enemies(caster)
    if effect.params.get("target_1_random_enemy"):
        enemies = get_enemies(caster)
        return random.sample(enemies, min(1, len(enemies))) if enemies else []
    if effect.params.get("target_2_random_enemies"):
        enemies = get_enemies(caster)
        return random.sample(enemies, min(2, len(enemies))) if enemies else []
    if effect.params.get("target_3_random_enemies"):
        enemies = get_enemies(caster)
        return random.sample(enemies, min(3, len(enemies))) if enemies else []
    if effect.params.get("target_2_random_allies"):
        allies = [h for h in caster.team.heroes if h.is_alive and h != caster]
        # If not enough non-caster allies, we can include the caster, but usually it meant other allies
        if len(allies) < 2:
            allies = [h for h in caster.team.heroes if h.is_alive]
        return random.sample(allies, min(2, len(allies)))
    return [caster] if effect.params.get("target_self") else [pick_target(caster)] or []

global_round = 0
all_heroes: List[Hero] = []

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
        # Expire CC states.
        for cc_name, state in list(hero.cc_states.items()):
            if isinstance(state, dict) and state.get("until", 0) <= global_round:
                del hero.cc_states[cc_name]
            elif isinstance(state, int) and state <= global_round:
                del hero.cc_states[cc_name]
        # Unseal if time is up
        if hero.flags.get("sealed_until", -1) <= global_round:
            hero.flags["passives_enabled"] = True


# ====================== TEAM & SIMULATION ======================
class Team:
    def __init__(self, heroes: List[Hero], number: int):
        self.heroes = heroes
        self.number = number
        for h in heroes:
            h.team = self
        self.opposite = None   # set after both teams created


def build_default_teams() -> tuple[Team, Team]:
    content_path = Path(__file__).parent / "data" / "game_content.json"
    source = JsonHeroContentSource(str(content_path))
    source.validate_effect_types(set(effect_handlers.keys()))

    runtime_factory = HeroRuntimeFactory(source, Hero, Skill, Passive, Effect)
    team1 = Team(runtime_factory.create_team_heroes("team1_default"), 1)
    team2 = Team(runtime_factory.create_team_heroes("team2_default"), 2)
    team1.opposite = team2
    team2.opposite = team1
    # Apply on_create passives after teams are set
    for hero in team1.heroes + team2.heroes:
        trigger_passives(hero, "on_create", {"current_round": 0})
    return team1, team2

def simulate_fight(team1: Team, team2: Team, max_rounds: int = 50):
    global global_round, all_heroes
    global_round = 0
    all_heroes = team1.heroes + team2.heroes
    team1.opposite = team2
    team2.opposite = team1

    print("=== FIGHT START ===\n")
    while any(h.is_alive for h in team1.heroes) and any(h.is_alive for h in team2.heroes) and global_round < max_rounds:
        print(f"Turn {global_round + 1}")
        # render_battle_ui(team1, team2, global_round)
        # Sort by speed descending, ties broken by team slot order
        acting_order = sorted(all_heroes, key=lambda h: (-h.speed, all_heroes.index(h)))
        
        for hero in acting_order:
            if not hero.is_alive:
                continue

            if hero.energy >= 100:
                over = hero.energy - 100
                bonus = over * 0.01   # +1% skill damage per excess energy
                if hero.active_skill:
                    execute_skill(hero, hero.active_skill, bonus)
                else:
                    execute_basic_attack(hero)
                hero.energy = 0
            else:
                execute_basic_attack(hero)

            # Trigger passives for this action
            trigger_passives(hero, "after_action")

        process_round_end(all_heroes)
        # Trigger turn_start passives
        for hero in all_heroes:
            if hero.is_alive:
                trigger_passives(hero, "turn_start", {"current_round": global_round})
        print("")

    print("\n=== FIGHT END ===")
    for t in [team1, team2]:
        alive = sum(1 for h in t.heroes if h.is_alive)
        print(f"Team {t.number}: {alive}/5 alive")


# ====================== RUN EXAMPLE ======================
if __name__ == "__main__":
    team1, team2 = build_default_teams()

    simulate_fight(team1, team2, max_rounds=30)
