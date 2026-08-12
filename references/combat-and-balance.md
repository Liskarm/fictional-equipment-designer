# Combat and Balance Reference

Use this reference when equipment participates in a fight, stealth encounter, tactical problem, or game loop. Separate fictional doctrine from game balance: they can support each other, but they are not the same claim.

## Contents

- [Role specification](#role-specification)
- [Interaction model](#interaction-model)
- [Counterplay ladder](#counterplay-ladder)
- [Game tuning model](#game-tuning-model)
- [Measurement and decision workflow](#measurement-and-decision-workflow)
- [Fictional doctrine model](#fictional-doctrine-model)
- [Variant balance matrix](#variant-balance-matrix)
- [QA and edge-case coverage](#qa-and-edge-case-coverage)
- [Playtest and narrative tests](#playtest-and-narrative-tests)

## Role specification

Complete this sentence before tuning:

> The equipment creates **[decision or opportunity]** at **[range/context]**, rewards **[skill or preparation]**, and is checked by **[counter or cost]**.

Record:

- primary and secondary roles;
- intended targets or problems;
- favorable and unfavorable environments;
- solo, team, vehicle, or institutional dependencies;
- entry condition and exit condition;
- deliberate non-role: what the item must not solve.

If two items share all six, they are functionally redundant even if their lore and appearance differ.

## Interaction model

### Operator or player verbs

Define what the user actually does: position, mark, prime, commit, sustain, redirect, vent, reload, coordinate, sacrifice, recover, or retreat. Prefer a small set of meaningful verbs over many passive bonuses.

### Pressure and commitment

- What exposure, delay, resource spend, vulnerability, or opportunity cost accompanies use?
- Can use be interrupted, baited, dodged, jammed, dispelled, outranged, overwhelmed, or made socially unacceptable?
- Does the user choose between immediate value and future readiness?
- What prevents repeated use from becoming the only correct action?

### Telegraphs and feedback

Specify preparation cue, active cue, impact cue, failure cue, and recovery cue. Decide which cues are visible or audible to the user, allies, opponents, and audience. Readability is part of fairness and visual design.

## Counterplay ladder

Provide counters at more than one layer:

1. **Awareness:** identify the item, state, or operator.
2. **Position:** use range, cover, terrain, formation, or timing.
3. **Resource:** exhaust charge, attention, ammunition, ritual conditions, or support.
4. **System:** disrupt sensing, control, supply, maintenance, authority, or magical compatibility.
5. **Strategic:** avoid the fight, change doctrine, regulate production, or attack infrastructure.

A counter should reduce dominance without making the equipment irrelevant. Distinguish hard counters, soft counters, and risky improvised responses.

## Game tuning model

Balance in this order:

1. **Role:** preserve a distinct reason to choose the item.
2. **Rules:** adjust access, commitment, vulnerability, targeting, or state transitions.
3. **Resources:** adjust availability, recovery, capacity, and competing uses.
4. **Feedback:** improve the user's ability to understand success and failure.
5. **Numbers:** tune output only after the previous layers are sound.

Useful tuning hooks include readiness, active duration, recovery, capacity, consumption, control forgiveness as a game abstraction, range band, area of influence, mobility, commitment, detectability, telegraph duration, reliability, ally dependency, upgrade cost, and opportunity cost.

When a game lacks established units, use a normalized baseline such as `standard item = 100` and label every value provisional. Do not convert normalized variables into real-world weapon specifications.

## Measurement and decision workflow

Treat tuning as a testable claim rather than a sequence of unexplained value changes. For each material change, record:

| Field | Required content |
|---|---|
| Tuning hypothesis | The role, decision, or readability problem; the proposed rule, resource, feedback, or provisional-value change; and why it should help. |
| Observable evidence | One metric and named game event when instrumentation exists, or a repeatable scene, observer behavior, or narrative beat as a qualitative proxy. |
| Segment and context | Relevant skill band, team composition, variant, encounter, map or scene type, input method, game version, and solo/team conditions. |
| Expected direction | The predicted increase, decrease, redistribution, or unchanged guardrail. Avoid unsupported universal targets. |
| Falsifier | A result that would reject the hypothesis, reveal role drift, or show that another item has lost relevance. |
| Collection constraints | Sample limits, opt-in or privacy rules, missing/duplicate events, bots, disconnects, version mixing, observer bias, and facts the available data cannot establish. |
| Owner and decision gate | Person or discipline responsible, review date or milestone, and the evidence required to approve, revise, hold, or revert the change. |

Use the smallest instrumentation that distinguishes the claim. An event contract should name the trigger, equipment and variant, encounter or scene context, operator state before and after, resource band, result, interruption or counter involved, and software/content version. Do not collect unrelated personal data. Verify that an event fires once at the intended transition before trusting aggregate results.

Recommended workflow:

1. **State the hypothesis:** tie the change to a role or interaction problem and name protected behaviors that should remain stable.
2. **Choose evidence:** select an observable event/metric or narrative proxy, the comparison segment, expected direction, and falsifier before testing.
3. **Constrain collection:** document inclusion rules, version window, missing-data risks, and whether the evidence supports correlation, causation, or only a design review.
4. **Validate instrumentation:** use a controlled setup to confirm event timing, state labels, variant identity, interruption/counter attribution, and absence of duplicate records.
5. **Run representative and adversarial cases:** include ordinary use, weakest intended user, expert use, favorable and unfavorable contexts, and counterplay.
6. **Decide at the gate:** the named owner records approve, revise and retest, hold for more evidence, or revert. Preserve the result and rationale so later tuning does not repeat a disproven assumption.

For narrative equipment, replace volume metrics with observable proxies: whether a reader can identify the decision and cost, whether a scene consistently exposes the weakness, whether an opponent has time to react, or whether independent reviewers infer the intended role. Label such evidence qualitative; do not imply statistical confidence.

## Fictional doctrine model

For narrative work, describe:

- unit or character type authorized to use it;
- training and selection;
- formation or team relationship;
- mission planning assumptions;
- transport and resupply burden;
- escalation, legality, stigma, or ritual significance;
- how enemies learn and adapt;
- how misuse exposes character, culture, or institutional failure.

Avoid declaring a doctrine “realistic” without evidence. Evaluate it against the setting's own constraints and any supplied historical analogies.

## Variant balance matrix

Use a relative matrix rather than “Mk II is better”:

| Variant | Role | Gains | Sacrifices | New counter | Resource burden | Skill demand |
|---|---|---|---|---|---|---|
| Base | | | | | | |
| Specialist | | | | | | |
| Low-cost/export | | | | | | |
| Prototype/elite | | | | | | |
| Field modification | | | | | | |

Keep at least one scenario where the base model remains preferable.

## QA and edge-case coverage

Write checks as **setup → action → expected outcome** and assign an owner. Cover at least the cases that can alter fairness, legibility, or state integrity:

| Case family | Minimum checks |
|---|---|
| Boundary | Empty, just-sufficient, near-capacity, and full resources; minimum/maximum valid range or area as game abstractions; zero and maximum valid targets; repeated use at a timing boundary. |
| State transition | Entry, commitment, active, impact, cancel, interruption, failure, recovery, and exit; rapid or repeated inputs; switching variant or context while a transition is pending. |
| Error and degraded operation | Invalid target, missing resource, lost authorization or fictional link, delayed/missing feedback, disconnect or scene interruption, and safe fallback without duplicated effects. |
| Friendly visibility | The user and allies can distinguish preparation, active, success, failure, and recovery; cues remain readable with overlapping allied effects and accessibility settings. |
| Opponent/audience visibility | Opponents or observers receive the promised reaction window and can attribute outcome to the correct item, operator, and counter; hidden-information rules reveal no extra state. |
| Interaction and counter | Each hard/soft counter, immunity or resistance rule, simultaneous effects, stacking priority, terrain/cover edge, weakest-user case, and expert-use case. |

Record each check in this form:

| QA ID | Setup | Action | Expected outcome | Observed evidence | Owner | Gate / disposition |
|---|---|---|---|---|---|---|
| | | | | | | Pass, fix, defer with risk, or block |

QA proves behavior in the tested setup, not universal balance. A passing functional check does not overrule a failed tuning hypothesis; a favorable aggregate metric does not excuse broken state transitions, unreadable opponent cues, or unsafe error handling. For prose and animation, use storyboard, scene, continuity, or reader-review equivalents and record the same setup/action/outcome logic.

## Playtest and narrative tests

Ask:

- Can an observer explain why the user succeeded or failed?
- Is there a meaningful decision before, during, and after activation?
- Does the item create a new play pattern instead of a larger number?
- Can opponents respond before the result is inevitable?
- Does its resource loop produce tension at the intended cadence?
- Does the weakest intended user still understand its state?
- Does expert use express mastery without deleting counterplay?
- Which encounter, enemy, or story scene exposes its weakness?
- Which existing item loses relevance, and how will its role be protected?
- What telemetry or scene evidence would falsify the current balance assumption?

