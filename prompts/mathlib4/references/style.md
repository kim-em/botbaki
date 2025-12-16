# Lean Library Style Guidelines

## Overview

This document outlines conventions for Lean library code to ensure uniform style and readability. These are guidelines rather than rigid rules.

## Variable Conventions

- **Universes**: `u`, `v`, `w`, ...
- **Generic types**: `α`, `β`, `γ`, ...
- **Propositions**: `a`, `b`, `c`, ...
- **Type elements**: `x`, `y`, `z`, ...
- **Assumptions**: `h`, `h₁`, ...
- **Predicates/relations**: `p`, `q`, `r`, ...
- **Lists**: `s`, `t`, ...
- **Sets**: `s`, `t`, ...
- **Natural numbers**: `m`, `n`, `k`, ...
- **Integers**: `i`, `j`, `k`, ...

Mathematical types use standard notation with uppercase letters (`G` for group, `R` for ring, `K`/`𝕜` for field, `E` for vector space).

## Line Length

Maximum line length is 100 characters for improved readability on smaller screens.

## Header and Imports

Files should begin with copyright information and author attribution:

```lean
/- Copyright (c) 2024 Joe Cool. All rights reserved. Released under
Apache 2.0 license as described in the file LICENSE.
Authors: Joe Cool -/
import Mathlib.Data.Nat.Basic
import Mathlib.Algebra.Group.Defs
```

Use `Authors` even for single contributors. Separate names with commas (no "and").

## Module Docstrings

After imports, include a module docstring (`/-! -/`) containing:

- File title
- Summary of contents (main definitions, theorems, proof techniques)
- Notation used (if applicable)
- Literature references (if applicable)

Example structure:

```lean
/-! # Foos and bars

In this file we introduce `foo` and `bar`, two main concepts in the
theory of xyzzyology.

## Main results

- `exists_foo`: the main existence theorem of `foo`s.
- `bar_of_foo_of_baz`: a construction of a `bar`, given a `foo` and
  a `baz`.

## Notation

- `|_|` : The barrification operator, see `bar_of_foo`.

## References

See [Thales600BC] for the original account on Xyzzyology.
-/
```

New bibliography entries go in `docs/references.bib`.

## Structuring Definitions and Theorems

All declarations and commands are top-level and appear flush-left (no indentation for namespace/section contents).

### Spacing Rules

- Use spaces around `:`, `:=`, and infix operators
- Place operators before line breaks, not at line beginnings
- Indent subsequent lines by 2 spaces after theorem statements
- For multi-line theorem statements, indent continuation lines by 4 spaces

Example:

```lean
theorem nat_case {P : Nat → Prop} (n : Nat)
    (H1 : P 0) (H2 : ∀ m, P (succ m)) : P n :=
  Nat.recOn n H1 (fun m IH ↦ H2 m)
```

### Proof Organization

Tactic mode proofs use `by` at the end of the preceding line:

```lean
theorem le_induction {P : Nat → Prop} {m}
    (h0 : P m) (h1 : ∀ n, m ≤ n → P n → P (n + 1)) :
    ∀ n, m ≤ n → P n := by
  apply Nat.le.rec
  · exact h0
  · exact h1 _
```

Short declarations fit on one line:

```lean
theorem succ_pos : ∀ n : Nat, 0 < succ n := zero_lt_succ
def square (x : Nat) : Nat := x * x
```

### Have Statements

Short justifications can share a line:

```lean
have h1 : n ≠ k := ne_of_lt h
```

Longer justifications go on the next line, indented:

```lean
have h1 : n ≠ k :=
  ne_of_lt h
```

With tactic mode, `by` stays on the same line:

```lean
have h1 : n ≠ k := by
  apply ne_of_lt
  exact h
```

### Structure and Class Definitions

Fields are indented 2 spaces with docstrings:

```lean
structure PrincipalSeg {α β : Type*} (r : α → α → Prop)
    (s : β → β → Prop) extends r ↪r s where
  /-- The supremum of the principal segment -/
  top : β
  /-- The image of the order embedding is the set of elements `b`
  such that `s b top` -/
  down' : ∀ b, s b top ↔ ∃ a, toRelEmbedding a = b
```

## Instances

Use `where` syntax for structure/class instances:

```lean
instance instOrderBot : OrderBot ℕ where
  bot := 0
  bot_le := Nat.zero_le
```

## Hypotheses Left of Colon

Prefer arguments left of the colon over universal quantifiers when the
proof introduces these variables:

```lean
-- Preferred
example (n : ℝ) (h : 1 < n) : 0 < n := by linarith

-- Less preferred
example (n : ℝ) : 1 < n → 0 < n := fun h ↦ by linarith
```

## Binders

Include a space after binders:

```lean
example : ∀ α : Type, ∀ x : α, ∃ y, y = x :=
  fun (α : Type) (x : α) ↦ Exists.intro x rfl
```

## Anonymous Functions

For simple functions, use centered dot syntax: `(· ^ 2)` for squaring.

For complex functions, use `fun` with `↦` (not `λ`):

```lean
fun x ↦ x * x
```

Avoid `lambda` notation in favor of `fun`.

## Calculations

Use `calc` with the keyword at the end of the preceding line:

```lean
theorem reverse_reverse : ∀ (l : List α), reverse (reverse l) = l
  | []     => rfl
  | a :: l => calc
      reverse (reverse (a :: l))
        = reverse (reverse l ++ [a]) := by rw [reverse_cons]
      _ = reverse [a] ++ reverse (reverse l) := reverse_append _ _
      _ = reverse [a] ++ l := by rw [reverse_reverse l]
      _ = a :: l := rfl
```

Relations should be aligned across lines; underscores should be left-justified.

## Tactic Mode

Place `by` at the end of the preceding line:

```lean
theorem continuous_uncurry_of_discreteTopology
    [DiscreteTopology α] {f : α → β → γ}
    (hf : ∀ a, Continuous (f a)) :
    Continuous (uncurry f) := by
  apply continuous_iff_continuousAt.2
  rintro ⟨a, x⟩
  change map _ _ ≤ _
  rw [nhds_prod_eq, nhds_discrete, Filter.map_pure_prod]
  exact (hf a).continuousAt
```

### Subgoals and Focusing

Use focusing dot `·` (not indented) for subgoals:

```lean
cases n
· simp only [Int.ofNat_eq_coe] at h
  rw [zpow_ofNat] at h
  refine ⟨_, Nat.pos_of_ne_zero fun n0 ↦ hn ?_, h⟩
  rw [n0]
  rfl
· rw [zpow_negSucc, inv_eq_one] at h
  refine ⟨_ + 1, Nat.succ_pos _, h⟩
```

Named subgoals can be proven in any order:

```lean
example {p q : Prop} (h₁ : p → q) (h₂ : q → p) : p ↔ q := by
  refine ⟨?imp, ?converse⟩
  case converse => exact h₂
  case imp => exact h₁
```

### Tactic Conventions

- One tactic per line (preferred)
- Use semicolons for short sequences: `cases bla; clear h`
- For sequential application: write on one line OR indent the next tactic

```lean
cases x <;>
  simp [a, b, c, d]
```

Very short proofs can use single-line tactic syntax:

```lean
example : ... := by by_cases h : x = 0; · rw [h]; exact hzero ha
```

## Squeezing Simp Calls

Terminal `simp` calls should NOT be squeezed (replaced with output from
`simp?`) unless performance is poor or the proof breaks. Reasons:

1. Squeezed calls are longer and obscure key lemmas
2. Squeezed calls break when lemmas are renamed

## Whitespace and Delimiters

Lean is whitespace-sensitive; avoid unnecessary delimiters.

Use `<|` and `|>` operators to avoid parentheses:

```lean
le_antisymm hxy <| le_of_forall_pos_le_add <| by
  intro ε hε
  have := h ε hε
  linarith
```

Dot notation with function application:

```lean
foo a |>.bar b |>.baz
```

Space after `←` in `rw` and `simp`:

```lean
rw [← add_comm a b]
simp [← and_or_left]
```

## Empty Lines in Declarations

Empty lines inside declarations are discouraged. Use comments instead.

## Normal Forms

Favor standardized forms throughout code. Example: use `s.Nonempty` over
equivalent alternatives.

**Special case**: For types with `⊥`/`⊤`, prefer `x ≠ ⊥` in assumptions
and `⊥ < x` in conclusions (easier to convert in one direction).

## Comments

- **Module sections**: Use `/-! -/` (included in auto-generated docs)
- **Technical comments**: Use `/- -/` for TODOs, implementation notes
- **Inline comments**: Use `--`

Documentation strings use `/-- -/` without indentation for multi-line content.

## Expressions in Messages

In printed messages, names and data should be:

- Inline and surrounded by backticks, OR
- On separate lines and indented

Example:

```
Could not find model with corners for domain
  src
nor codomain
  tgt
of function
  f
```

## Deprecation

When removing/renaming public declarations, use `@[deprecated]`:

```lean
@[deprecated (since := "YYYY-MM-DD")]
alias old_name := new_name

@[deprecated "Explanation of transition..." (since := "YYYY-MM-DD")]
theorem example_thm ...
```

For `to_additive` declarations, deprecate both versions:

```lean
@[deprecated (since := "YYYY-MM-DD")]
alias AddGroup_foo := AddGroup_bar

@[to_additive existing, deprecated (since := "YYYY-MM-DD")]
alias Group_foo := Group_bar
```

Deprecations can be removed after 6 months. Named instances don't require deprecations.
