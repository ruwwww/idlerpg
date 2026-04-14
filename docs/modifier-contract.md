# Modifier Contract

This file defines the canonical status modifier keys used by the combat engine.

## Design Rules

- Prefer status `data.modifiers` for direct values.
- Prefer status `data.modifiers_per_stack` for stack-scaled values.
- Keep values additive. The engine combines all sources before applying formulas.
- Use decimal values (0.15 = 15%).

## Supported Structures

Direct values:

```json
{
  "data": {
    "modifiers": {
      "healing_reduction": 0.15,
      "shield_reduction": 0.15
    }
  }
}
```

Stack-based values:

```json
{
  "data": {
    "modifiers_per_stack": {
      "damage_reduction": {
        "shroom_potion": 0.15
      }
    }
  }
}
```

Legacy compatibility still works:

- `data.<key>`
- `data.<key>_per_stack`

## Canonical Keys

Damage intake:

- `damage_reduction`
- `shield_damage_reduction`
- `dot_damage_reduction`
- `damage_taken_up`
- `damage_taken_down`

Healing intake:

- `healing_reduction`
- `heal_reduction`
- `healing_received_mult`
- `heal_received_mult`

Shield intake:

- `shield_reduction`
- `shielding_reduction`
- `shield_received_mult`
- `shielding_received_mult`

Core stats:

- `atk_mult`
- `speed_delta`
- `cc_immunity_chance`
- `dot_damage_mult`

## Formula Summary

Damage final:

- Apply reduction modifiers first.
- Apply type-specific reduction (for DoT).
- Apply damage taken down/up.

Healing final:

- `final = base * max(0, 1 + heal_up - heal_down)`

Shield final:

- `final = base * max(0, 1 + shield_up - shield_down)`

## Notes

- For long-duration dispellable debuffs, use high duration values (for example `999`) and include `debuff` in status tags.
- Keep mechanic semantics in data keys, not in hardcoded handler branches.
