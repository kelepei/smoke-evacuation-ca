# C-D-B Per-Step Behavior Contract

This is a D-side integration contract.  It does not change C, B, or A code.

## Why it exists

The final web page already reads A's Grid, C's positioned people/relations
file and YAML configuration, then calls B's `EvacEngine.run_one_step`.
For B to use C's group, herding, guide, or information results in the same
step, D needs one actual mapping per person.  D must not recreate C's social
graph because that could differ from C's exported relationships.

## Provider shape requested from C

Please provide a callable that receives the current B engine and returns one
of these shapes:

```python
{person_id: {"is_waiting": False, "herding_influence": 0.4}}

# or a mergeable named-engine result
{
    "group": {person_id: {"is_following": True, "follow_target": 3}},
    "herd": {person_id: {"herding_influence": 0.4, "dominant_direction": [1, 0]}},
    "guide": {person_id: {"guide_influence": 0.6}},
}
```

`person_id` must be C's exported source ID.  D maps it to B's runtime ID
through `source_person_id`, so C's current zero-based IDs are supported.
Only values C actually calculates should be returned.  Missing values are not
filled by D.

## D-side use

`experiments.c_behavior_adapter.CStepBehaviorAdapter(provider)` can be passed
as the `behavior_provider` of `EvacEngineRuntimeAdapter`.  It combines C's
named engine outputs per person and sends B the required
`{runtime_person_id: behavior}` mapping.  Until C supplies this provider, the
runtime deliberately calls B with `{}` and labels C per-step behavior as not
connected.
