# Putnam test questions (in natural language, via the UI)

20 Putnam problems, written as questions you can paste into the **New research** form in the UI. Source: [PutnamBench](https://github.com/trishullab/PutnamBench) (Lean statements Apache-2.0; natural-language texts published in PutnamBench with MAA permission).

## How to run
1. Open **http://127.0.0.1:8765** in your browser.
2. **New research** → Field: **Mathematics** → paste the **text in the box** under each problem into the "Research question" field (do not copy the quoted original statement or the heading).
3. **Budget cap:** $2–3 is recommended. The proof search uses at most half of the budget; the rest is for review, verification and the report.
4. **Autonomy:** for the first runs, use "Ask at every step". The team waits for you to pick a hypothesis and approve experiments; you will see the scored hypothesis cards.
5. If the research ends inconclusively, you can change the hypothesis or approach from the **Research tree** tab and open a new branch, or choose "Let the Director iterate".

## What to expect
- These questions are competition level. Here the team **formalizes the statement itself**; it does not use a ready-made Lean statement as the measurement did. So the result may differ from the measurement. Geometry and analysis questions (B1, B6, A4 1967, 2013 A5, 2017 A3) are very hard to formalize in Lean.
- An "Inconclusive" or "limited scope" result is not a failure; it is an honest result. What you should actually check:
  - **Proof tab:** does the formal statement encode the question correctly?
  - **Report:** is anything overstated?
  - **Exploration:** were counterexamples searched for in small cases?
  - **Back-translation:** does the independent reading match the hypothesis?
- If something goes wrong, use **Report a problem** on the project page.

## Measurement results (with the ready-made Lean statement, 2026-10-04)
"Old": single-shot proof. "New": automation + robust attempts + sketch/lemma search and high effort. Only the first 9 problems were measured with the new strategy; then the measurement was stopped. "Limit": the model exceeded its output limit and no attempt could be made.

| # | Problem | Old | New | Topic |
| --- | --- | --- | --- | --- |
| 1 | 1964 B1 | ✅ | ✅ | analysis, series |
| 2 | 1965 A2 | ❌ limit | ❌ | combinatorial identity |
| 3 | 1965 B3 | ✅ | ✅ | number theory, triangles |
| 4 | 1966 A4 | not measured | not measured | number theory |
| 5 | 1966 A6 | ❌ limit | ✅ | nested radicals |
| 6 | 1967 A1 | ✅ | ✅ | trigonometric polynomial |
| 7 | 1967 A4 | ❌ limit | ❌ | integral equation |
| 8 | 1967 B1 | ❌ limit | ❌ | geometry |
| 9 | 1970 B6 | ❌ | ❌ | geometry |
| 10 | 1976 A6 | ✅ | — | analysis, derivative |
| 11 | 1979 A5 | ❌ limit | — | floor function, roots |
| 12 | 1993 A2 | ✅ | — | sequences |
| 13 | 1996 A4 | ✅ | — | combinatorics, ordering |
| 14 | 1999 A6 | ❌ limit | — | recurrence, divisibility |
| 15 | 2000 B5 | ✅ | — | sets, mod 2 |
| 16 | 2002 B3 | ❌ limit | — | inequality, e |
| 17 | 2009 B1 | ✅ | — | primes, factorials |
| 18 | 2012 A2 | ✅ | — | algebra, operations |
| 19 | 2013 A5 | ❌ | — | geometry, areas |
| 20 | 2017 A3 | ❌ limit | — | integral, limit |

Suggestion: try the **unsolved ones** (2, 7, 8, 9, 11, 14, 16, 19, 20) first; working in natural language with exploration and refutation steps, the team may approach them differently. As a control, also try **one of the solved ones** (e.g. 12 or 18).

---

## Questions (copy-paste)

### 1 · 1964 B1
```text
For a sequence (aₙ) of positive integers, if the series ∑ 1/aₙ converges and bₙ is the number of terms of the sequence that are less than or equal to n, is it true that the ratio bₙ/n tends to 0 as n goes to infinity?
```
> Let $a_n$ be a sequence of positive integers such that $\sum 1/a_n$ converges. For all $n$, let $b_n$ be the number of $a_n$ which are at most $n$. Prove that $\lim b_n/n = 0$.

### 2 · 1965 A2
```text
For every positive integer n, is the sum of ((n − 2r)/n · C(n, r))² from r = 0 to ⌊(n−1)/2⌋ equal to (1/n) · C(2n − 2, n − 1)?
```
> Prove that $\sum_{r=0}^{\lfloor (n-1)/2\rfloor} \left(\frac{n-2r}{n}\binom{n}{r}\right)^2 = \frac{1}{n}\binom{2n-2}{n-1}$ for every positive integer $n$.

### 3 · 1965 B3
```text
Is it true that there are exactly three right triangles (up to rotation and translation) with integer side lengths whose area equals twice their perimeter?
```
> Prove that there are exactly three right triangles (up to orientation and translation) with integer side lengths and area equal to twice their perimeter.

### 4 · 1966 A4
```text
If we list the positive integers that are not perfect squares in increasing order, is the n-th term n + {√n}? ({m} is the integer closest to m.)
```
> Prove that the $n$th item in the ascending list of non-perfect-square positive integers equals $n + \{\sqrt{n}\}$, where $\{m\}$ denotes the closest integer to $m$.

### 5 · 1966 A6
```text
Is the infinite nested radical √(1 + 2√(1 + 3√(1 + 4√(1 + 5√(…))))) equal to 3?
```
> Prove that $\sqrt{1 + 2\sqrt{1 + 3\sqrt{1 + 4\sqrt{1 + 5\sqrt{\dots}}}}} = 3$.

### 6 · 1967 A1
```text
Let a₁,…,aₙ be real numbers and f(x) = a₁ sin x + a₂ sin 2x + … + aₙ sin nx. If |f(x)| ≤ |sin x| for every real x, is it true that |a₁| + |2a₂| + … + |naₙ| ≤ 1?
```
> Let $f(x)=a_1\sin x+\dots+a_n\sin nx$. Given that $|f(x)| \le |\sin x|$ for all real $x$, prove that $|a_1|+|2a_2|+\dots+|na_n| \le 1$.

### 7 · 1967 A4
```text
If λ > 1/2, is it true that there is no real-valued function u satisfying u(x) = 1 + λ ∫ₓ¹ u(y) u(y − x) dy for every x in the interval [0, 1]?
```
> Show that if $\lambda > 1/2$ there does not exist a real-valued function $u$ such that for all $x \in [0,1]$, $u(x)=1+\lambda\int_x^1 u(y)u(y-x)\,dy$.

### 8 · 1967 B1
```text
In a hexagon ABCDEF inscribed in a circle of radius r, if AB = CD = EF = r, are the midpoints of the sides BC, DE and FA the vertices of an equilateral triangle?
```
> Let $ABCDEF$ be a hexagon inscribed in a circle of radius $r$. If $AB = CD = EF = r$, prove that the midpoints of $BC$, $DE$, $FA$ form an equilateral triangle.

### 9 · 1970 B6
```text
If a circle can be inscribed in a quadrilateral with side lengths a, b, c, d and area √(abcd), is it true that the quadrilateral can be inscribed in a circle (is a cyclic quadrilateral)?
```
> Prove that if a quadrilateral with side lengths $a,b,c,d$ and area $\sqrt{abcd}$ has an inscribed circle, then it is cyclic.

### 10 · 1976 A6
```text
If f: ℝ → ℝ is twice continuously differentiable, |f(x)| ≤ 1 for every x, and (f(0))² + (f′(0))² = 4, is it true that there is a real number y such that f(y) + f″(y) = 0?
```
> Suppose $f$ is twice continuously differentiable, $|f(x)| \le 1$ for all $x$, and $f(0)^2 + f'(0)^2 = 4$. Prove that $f(y) + f''(y) = 0$ for some real $y$.

### 11 · 1979 A5
```text
Let S(x) be the sequence ⌊0⌋, ⌊x⌋, ⌊2x⌋, ⌊3x⌋, …. Does the polynomial x³ − 10x² + 29x − 25 have two distinct real roots α and β such that infinitely many positive integers appear in both S(α) and S(β)?
```
> Let $S(x)$ denote the sequence $\lfloor 0\rfloor, \lfloor x\rfloor, \lfloor 2x\rfloor, \dots$. Prove that there exist distinct real roots $\alpha, \beta$ of $x^3-10x^2+29x-25$ such that infinitely many positive integers appear in both $S(\alpha)$ and $S(\beta)$.

### 12 · 1993 A2
```text
Let (xₙ) be a sequence of nonzero real numbers such that xₙ² − xₙ₋₁xₙ₊₁ = 1 for n ≥ 1. Is there a real number a such that xₙ₊₁ = a·xₙ − xₙ₋₁ for every n ≥ 1?
```
> Let $(x_n)$ be nonzero reals with $x_n^2 - x_{n-1}x_{n+1} = 1$ for $n \ge 1$. Prove there exists a real $a$ such that $x_{n+1} = a x_n - x_{n-1}$ for all $n \ge 1$.

### 13 · 1996 A4
```text
Let A be a finite set and S a set of ordered triples (a, b, c) of distinct elements of A. Suppose the following three conditions hold:
- (a, b, c) ∈ S if and only if (b, c, a) ∈ S;
- (a, b, c) ∈ S if and only if (c, b, a) ∉ S;
- (a, b, c) and (c, d, a) are both in S if and only if (b, c, d) and (d, a, b) are both in S.

Then, is there a one-to-one function g from A to ℝ such that g(a) < g(b) < g(c) implies (a, b, c) ∈ S?
```
> Let $S$ be a set of ordered triples of distinct elements of a finite set $A$ satisfying the three conditions above. Prove there is a one-to-one $g: A \to \mathbb{R}$ such that $g(a)<g(b)<g(c)$ implies $(a,b,c) \in S$.

### 14 · 1999 A6
```text
For the sequence defined by a₁ = 1, a₂ = 2, a₃ = 24 and aₙ = (6aₙ₋₁²aₙ₋₃ − 8aₙ₋₁aₙ₋₂²) / (aₙ₋₂aₙ₋₃) for n ≥ 4, is it true that aₙ is an integer multiple of n for every n?
```
> The sequence is defined by $a_1=1, a_2=2, a_3=24$ and $a_n = \frac{6a_{n-1}^2a_{n-3} - 8a_{n-1}a_{n-2}^2}{a_{n-2}a_{n-3}}$ for $n\ge4$. Show that for all $n$, $a_n$ is an integer multiple of $n$.

### 15 · 2000 B5
```text
Let S₀ be a finite set of positive integers. Define the set Sₙ₊₁ as follows: a ∈ Sₙ₊₁ if and only if exactly one of a − 1 and a is in Sₙ. Are there infinitely many integers N satisfying S_N = S₀ ∪ {N + a : a ∈ S₀}?
```
> Let $S_0$ be a finite set of positive integers; $a \in S_{n+1}$ iff exactly one of $a-1$, $a$ is in $S_n$. Show that there exist infinitely many $N$ with $S_N = S_0 \cup \{N + a : a \in S_0\}$.

### 16 · 2002 B3
```text
For every integer n > 1, does the inequality 1/(2ne) < 1/e − (1 − 1/n)ⁿ < 1/(ne) hold?
```
> Show that for all integers $n > 1$, $\frac{1}{2ne} < \frac{1}{e} - (1 - \frac{1}{n})^n < \frac{1}{ne}$.

### 17 · 2009 B1
```text
Can every positive rational number be written as a quotient of products of factorials of primes (repetitions allowed)? Example: 10/9 = (2! · 5!) / (3! · 3! · 3!).
```
> Show that every positive rational number can be written as a quotient of products of factorials of (not necessarily distinct) primes.

### 18 · 2012 A2
```text
Let * be a commutative and associative operation on a set S such that for every x, y in S there is a z ∈ S with x * z = y. Then, for a, b, c in S, is it true that a * c = b * c implies a = b?
```
> Let $*$ be a commutative and associative operation on $S$ such that for all $x, y$ there is $z$ with $x*z = y$. Show that $a*c = b*c$ implies $a = b$.

### 19 · 2013 A5
```text
For m ≥ 3, if a list of C(m, 3) real numbers aᵢⱼₖ with 1 ≤ i < j < k ≤ m satisfies ∑ aᵢⱼₖ · Area(AᵢAⱼAₖ) ≥ 0 for every choice of points A₁, …, Aₘ in the plane ("area definite" for ℝ²), does the same inequality also hold for every choice of points in three-dimensional space?
```
> Prove that if a list of $\binom{m}{3}$ numbers $a_{ijk}$ is area definite for $\mathbb{R}^2$, then it is area definite for $\mathbb{R}^3$.

### 20 · 2017 A3
```text
Let a < b, let f and g be continuous functions from the interval [a, b] to (0, ∞), with ∫ₐᵇ f = ∫ₐᵇ g but f ≠ g. Is the sequence Iₙ = ∫ₐᵇ f(x)ⁿ⁺¹ / g(x)ⁿ dx increasing, and does Iₙ go to infinity as n goes to infinity?
```
> Let $f, g$ be continuous from $[a,b]$ to $(0,\infty)$ with $\int f = \int g$ but $f \ne g$. Let $I_n = \int_a^b f^{n+1}/g^n$. Show that $I_n$ is increasing with $\lim I_n = \infty$.
