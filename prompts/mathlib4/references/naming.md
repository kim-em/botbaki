# Mathlib Naming Conventions (Lean 4)

## File Names

Lean files should use `UpperCamelCase`. Rare exceptions exist for specifically lowercased objects (e.g., `lp.lean` for the ℓₚ space), requiring prior discussion on Zulip.

## General Conventions

### Capitalization

Mathlib4 employs mixed casing based on declaration type:

1. **Propositions and proofs**: `snake_case`
2. **Types and type constructors**: `UpperCamelCase` (rare exceptions for structure fields)
3. **Functions**: Named per their return type convention
4. **Other type-valued terms**: `lowerCamelCase`
5. **UpperCamelCase items within snake_case contexts**: Referenced in `lowerCamelCase`
6. **Acronyms**: Written as groups (e.g., `LE`, `Ne` for symmetry with `Eq`)

Examples include `OneHom` (structure), `map_one'` (field), and `MonoidHom.toOneHom_injective` (theorem with alignment).

### Spelling

American English spelling is standard: `factorization`, `Localization`, `FiberBundle`.

### Symbol Dictionary

#### Logic

| Symbol | Name | Notes |
|--------|------|-------|
| `∨` | `or` | |
| `∧` | `and` | |
| `→` | `imp`/`of` | Conclusion stated first |
| `↔` | `iff` | Sometimes omitted |
| `¬` | `not` | |
| `∀` | `forall`/`all` | Use `ball` for bounded |
| `∃` | `exists` | Use `bex` for bounded |
| `=` | `eq` | Often omitted |
| `≠` | `ne` | |

#### Set Operations

| Symbol | Name | Notes |
|--------|------|-------|
| `∈` | `mem` | |
| `∪` | `union` | |
| `∩` | `inter` | |
| `⋃` | `iUnion` | Indexed version |
| `⋂` | `iInter` | Indexed version |
| `\` | `sdiff` | Set difference |
| `ᶜ` | `compl` | Complement |

#### Algebra

| Symbol | Name | Notes |
|--------|------|-------|
| `+` | `add` | |
| `-` | `sub`/`neg` | `neg` for unary, `sub` for binary |
| `*` | `mul` | |
| `^` | `pow` | |
| `/` | `div` | |
| `•` | `smul` | Scalar multiplication |
| `∣` | `dvd` | Divisibility |

#### Lattices

| Symbol | Name | Notes |
|--------|------|-------|
| `≤` | `le`/`ge` | `ge` for swapped arguments |
| `<` | `lt`/`gt` | `gt` for swapped arguments |
| `⊔` | `sup` | Binary supremum |
| `⊓` | `inf` | Binary infimum |
| `⨆` | `iSup` | Indexed supremum |
| `⨅` | `iInf` | Indexed infimum |

### Comparison Operator Naming

Use `ge`/`gt` when:
- Arguments to `≤` or `<` appear in different orders
- Matching argument order of another relation
- Describing the swapped relation
- The second argument is "more variable"

## Identifiers and Theorem Names

Theorems use descriptive names matching their conclusions:

```lean
#check succ_ne_zero  -- conclusion describes what's proven
#check mul_zero      -- short form when prefix suffices
```

### Hypothesis Ordering

When describing hypotheses, use "of" to separate them. For theorem `A → B → C`, use name `C_of_A_of_B` (hypotheses in appearance order, not reversed).

### Common Abbreviations

- `pos`, `neg`, `nonpos`, `nonneg` replace `zero_lt`, `lt_zero`, etc.
- "Left" and "right" variants help distinguish theorem forms
- Namespace removal applies when referencing definitions in other namespaces

## Structural Lemmas

### Extensionality

- `.ext`: Lemma of form "(∀ x, f x = g x) → f = g", marked with `@[ext]`
- `.ext_iff`: Bidirectional version

### Injectivity

- `f_injective`: Primary form using `Function.Injective f`
- `f_inj`: Bidirectional implication; good for `@[simp]`
- `.inj`: Automatically generated constructors
- `.inj_iff`: Bidirectional variant when needed

Use "left" or "right" to specify which argument changes in the equivalence.

### Induction and Recursion Principles

| Motive Eliminates Into | Value First | Constructions First |
|----------------------|-------------|-------------------|
| `Prop` | `T.induction_on` | `T.induction` |
| `Sort u` / `Type u` | `T.recOn` | `T.rec` |

### Predicate Positioning

Most predicates are prefixes (e.g., `isClosed_Icc`). Exceptions include widely-used ones analogous to suffixed atoms:
- `_injective`, `_surjective`, `_bijective`
- `_monotone`, `_antitone`, `_strictMono`, `_strictAnti`

### Prop-Valued Classes

- Noun-based: Begin with "Is" (e.g., `IsNormal`, `IsTopologicalRing`)
- Adjective-based: No "Is" prefix needed

### Function Variants

Distinguish unexpanded (`fun x ↦ f x * g x`) from expanded (`f * g`) forms:
- Expanded form: Use `fun_mul`, `fun_add`, etc.
- Unexpanded form: Use standard naming (`mul`, `add`)

Example: `Continuous.fun_mul` vs. `Continuous.mul`

## Variable Conventions

- **Universes**: `u`, `v`, `w`
- **Types**: `α`, `β`, `γ` (or mathematical notation: `G`, `R`, `K`/`𝕜`, `E`)
- **Propositions**: `a`, `b`, `c`
- **Elements**: `x`, `y`, `z`
- **Assumptions**: `h`, `h₁`, `h₂`
- **Predicates**: `p`, `q`, `r`
- **Lists/Sets**: `s`, `t`
- **Naturals**: `m`, `n`, `k`
- **Integers**: `i`, `j`, `k`
