# Mathlib Review Conventions

## Generalization and Assumptions

- **State results under the weakest assumptions actually used.**
  State lemmas and instances with the minimal typeclasses and
  hypotheses required by the proof. Prefer `Semiring` over `Ring`,
  `Preorder` over `LinearOrder`, `Finite` over `Fintype`, `Injective`
  over `Bijective`, etc. Remove unused assumptions.

- **Prove the general theorem first; derive special cases afterward.**
  Do not duplicate the proof for the special case.

## API Design

- **Reuse existing Mathlib abstractions.** Before adding a new
  definition, theorem, or wrapper, search Mathlib for the canonical
  existing concept and reuse it. Do not create parallel APIs that
  differ only by naming or packaging.

- **Hide implementation details behind user-facing lemmas.**
  Public statements should use the intended mathematical interface,
  not implementation artifacts such as quotient representatives,
  `Classical.choose`, or auxiliary internal definitions.

- **Give every new public definition a usable API immediately.**
  Provide `_apply` lemmas, `*_iff` characterizations, extensionality,
  identity/composition laws, and key instances.

## File Organization

- **Put declarations in their canonical home.** Place each new
  definition, theorem, or instance in the file whose mathematical
  topic, abstraction level, and dependencies best match it.

## Proof Architecture

- **Factor repeated or substantial arguments into helper lemmas.**
  Extract repeated reasoning into helper lemmas, often `private` if not
  needed by downstream users.

- **Prefer explicit API lemmas over fragile definitional equality.**
  Do not rely on accidental definitional equality. Prove and use named
  lemmas instead.

- **Reuse existing lemmas and APIs before writing long proofs.**
  If a proof is longer than expected, search for a theorem that already
  expresses the needed fact.
