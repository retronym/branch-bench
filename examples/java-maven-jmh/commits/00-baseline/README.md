# branch-bench-sample

A sample Java + Maven + JMH project demonstrating [branch-bench](https://github.com/jasonzaugg/branch-bench) — a tool that walks a git branch commit-by-commit, runs JMH benchmarks at each step, and renders an interactive HTML report showing performance trends over time.

## What this repo shows

Each commit on `main` is a small change to `PropertyResolver`, a class that expands `${key}` placeholders in strings:

| Commit | Change |
|--------|--------|
| Baseline | Compiles a new `Pattern` per property on every `resolve()` call |
| StringBuilder | Single-pass char scan, no regex — ~10× faster |
| char[] micro-opt | Scan over `char[]` instead of `charAt()` — marginal gain |
| Comment | Complexity note added — **no logic change** (flat line) |
| Regression | `String +=` in loop — O(n²) allocations, clear regression |

## Live report

👉 **[retronym.github.io/branch-bench-sample](https://retronym.github.io/branch-bench-sample/)**

## Quickstart

### 1. Clone

```bash
git clone https://github.com/retronym/branch-bench-sample.git
cd branch-bench-sample
```

### 2. Install branch-bench

```bash
pipx install branch-bench
```

Or with pip:

```bash
pip install branch-bench
```

### 3. Run

```bash
branch-bench run      # walk all commits, build + benchmark each one
branch-bench report   # generate .bench/epoch-1/index.html
branch-bench show     # open report in browser
```

## Prerequisites

- Java 21+
- Maven 3.8+
- Python 3.11+ (for branch-bench)
