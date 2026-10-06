import Mathlib

open Finset BigOperators

/-- The sum of the first n odd numbers is n²: 1 + 3 + ... + (2n - 1) = n². -/
theorem sum_first_odds (n : ℕ) : ∑ i ∈ range n, (2 * i + 1) = n ^ 2 := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [sum_range_succ, ih]
    ring
