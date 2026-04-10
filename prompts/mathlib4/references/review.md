# Mathlib Review Conventions

Structural review guidelines drawn from an [analysis of ~94k GitHub PR
review comments and ~165k Zulip messages](https://github.com/Vilin97/mathlib-conventions).
The number in parentheses is how many times the convention was raised
across those sources.

## Generalization and Assumptions

- **State results under the weakest assumptions actually used (453).**
  Use the minimal typeclasses and hypotheses required by the proof.
  Prefer `Semiring` over `Ring`, `Preorder` over `LinearOrder`,
  `Finite` over `Fintype`, `Injective` over `Bijective`, etc.
  Remove unused assumptions.

- **Use the weakest sufficient typeclass assumptions (302).**
  State lemmas and instances with the minimal algebraic, order,
  topological, or categorical assumptions actually used.

- **Prove the general theorem first; derive special cases afterward
  (265).** Do not duplicate the proof for the special case.

## API Design

- **Reuse existing Mathlib abstractions (284).** Before adding a new
  definition, theorem, or wrapper, search Mathlib for the canonical
  existing concept and reuse it. Do not create parallel APIs that
  differ only by naming or packaging.

- **Hide implementation details behind user-facing lemmas (175).**
  Public statements should use the intended mathematical interface,
  not quotient representatives, `Classical.choose`, or internal
  wrappers.

- **Mirror existing APIs when adding analogous constructions (135).**
  Copy the established naming pattern, assumptions, companion lemmas,
  and simp behavior.

- **Give every new public definition a usable API immediately (103).**
  Provide `_apply` lemmas, `*_iff` characterizations, extensionality,
  identity/composition laws, and key instances.

## File Organization

- **Put declarations in their canonical home (730).** Place each new
  definition, theorem, or instance in the file whose mathematical
  topic, abstraction level, and dependencies best match it.

- **Minimize imports (108).** Import only the modules actually needed.
  Do not rely on transitive imports.

## Proof Architecture

- **Factor repeated or substantial arguments into helper lemmas (228).**
  Extract repeated reasoning into helper lemmas, often `private` if
  not public API.

- **Prefer explicit API lemmas over fragile definitional equality
  (133).** Do not rely on accidental definitional equality across
  wrappers or coercions. Prove and use named lemmas instead.

- **Reuse existing lemmas and APIs before writing long proofs (174).**
  If a proof is longer than expected, search for a theorem that already
  expresses the needed fact.
