# An exact Jervis version in a Puretelligence research packet

This is a **new public release adapter**, not a recovered historical Quant validator. Its purpose is to make one architecture boundary runnable and inspectable without a private market warehouse or Registry.

```text
invented source → Jervis StateStore candidate versions K0 / K1 / K2
                 → Registry.get_version(object ID, exact version)
                 → Memory._body attachment integrity check
                 → owner / scope / available_at checks
                 → Puretelligence research packet with resolved reference
                 → replay on a second invented price path
```

Run `python examples/run_experience_bridge.py` after `python -m pip install '.[test,jervis]'`. The optional dependency is pinned to Jervis commit `0abc7693dee0d457a84e01e9d5fdd83ee46ecc88`. The script writes only under a fresh `.demo/experience-bridge/` or an empty `--output` directory. Jervis's `StateStore` initializes tables and an index, so this example uses its own database rather than opening a production Registry.

The real Jervis primitives used here are `StateStore.save`, `Registry.get_version`, `Registry.list_versions`, and `Memory._body`. The small `resolve_jervis_experience` adapter is new public release code. It checks an exact source object and version as well as the experience body. `build_research_context` then stores the verified reference and a separately resolved body in the public decision packet. **The research context's `evidence_refs` alone only normalizes strings; it does not read Jervis.** The adapter call is the actual resolution step.

K0 contains a cash comparison. K1 changes the active candidate text to ask for independent sell, buy and switch comparisons. K2 records a proposed shortcut and a `no_update` outcome while preserving K1's active text. Those three synthetic records are written by the fixture with `StateStore.save`; the fixture does not claim a model or historical trader made the judgments. All three remain Jervis `candidate` objects. An additional test resolves K0 after K2 exists and rejects wrong owner, wrong scope, a future version, a missing version, and a modified attachment.

`decision_packet.json` is inspectable evidence that the exact Jervis candidate was resolved before it was included in the synthetic Guanlan research packet. `retest.json` compares hold A, sell A to cash, switch A to B, and buy B with existing cash on a **different invented** market path. Its actions are fixed inputs. The comparison cannot establish that a learning trader changed behavior, that the material was truly unseen, or that any trade would have filled in a live market.

The local historical Lu Dongyangzi loop used a continuing trader, real local case coordination, Jervis candidate state, and later diagnostic runs. Its private coordinator, complete K0/K1 snapshots, and input-time validation source are not part of this public slice. The historical record and its limits are described in the [portfolio case](https://github.com/Cornelius-Chen/Cornelius-Chen/blob/main/cases/quant.md). This new bridge must not be retroactively attributed to that run.
