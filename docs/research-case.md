# Puretelligence research case

## Question

Can a continuing research trader separate what was knowable at a decision point from what became visible afterward, compare materially different actions, and retain a revisable judgment without claiming premature learning?

## Architecture

The market system owns source provenance, availability times, snapshot readiness, replay, cash/inventory accounting, and diagnosis. Jervis owns a versioned, source-linked candidate experience for a continuing trader identity. A short-lived worker can receive a scoped packet; the worker is not the authority for market data or promotion. The public example exercises these boundaries with fixed actions and invented inputs. It does not run the historical worker or coordinator.

![Illustrated research mechanism showing cutoff evidence, independent actions, later replay and provisional experience](../assets/puretelligence-research-atlas.png)

[Editable technical sketch](../assets/puretelligence-research-loop.svg)

## Method and observations

1. Create a time-bounded synthetic snapshot and refuse a replay when a price is changed after the snapshot hash was recorded.
2. Admit a research note only if its availability time is no later than the decision cutoff.
3. Replay four hypotheses under the same accounting assumptions: hold A, sell A to cash, buy B with existing cash, and switch A into B.
4. Diagnose the difference between B beating cash and B beating an existing holding. In the first invented path, the switch trails holding A; a second invented path favors the switch. This is a sensitivity demonstration, not a performance estimate.
5. Save a revised research question and an unpromoted candidate record. The optional Jervis bridge pins an exact candidate version and its source before another fixed-action replay.

The generated files in `.demo/research/` and `.demo/experience-bridge/` expose the packet, scenario comparison, diagnosis, candidate and run receipt. Run the commands in the [README](../README.md) to reproduce them.

## Historical research and limits

The larger local study used one continuing trader, deterministic market queries, short-lived Jervis worker reasoning, simulated cases, weak-point diagnosis, targeted rereading and new-material retests. The candidate revision stayed provisional. The public release does not include the private coordinator, complete candidate history, private market warehouse, live broker connection or a blind human-rated gain study. Simulated next-minute quotes are not executed fills. A completed replay and a stored experience version are engineering observations, not proof of improved returns.

The release source keeps the original internal module names for provenance. The public presentation emphasizes the research questions and evidence path rather than those internal labels.
