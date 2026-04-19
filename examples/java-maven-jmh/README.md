# java-maven-jmh sample

A minimal Java + Maven + JMH project demonstrating branch-bench on a two-commit
performance improvement history.

## What it shows

| Commit | Change | Expected trend |
|--------|--------|----------------|
| `00-baseline` | Compiles a new `Pattern` per property on every `resolve()` call | slow |
| `01-stringbuilder` | Single-pass `StringBuilder` scan, no regex allocation | ~10× faster |
| `02-chararray` | Converts input to `char[]` upfront to reduce `charAt()` bounds-check overhead | marginal improvement |
| `03-comment` | Adds a complexity comment — **no logic change** | flat (no-op) |
| `04-concat-regression` | Replaces `StringBuilder` with `String +=` in loop — O(n²) allocations | clear regression |

## Quickstart

### 1. Generate the sample git repo

```bash
cd examples/java-maven-jmh
./generate-repo.sh          # writes to /tmp/branch-bench-sample
```

Pass a path to write elsewhere:

```bash
./generate-repo.sh ~/branch-bench-sample
```

### 2. Run branch-bench

```bash
cp examples/java-maven-jmh/bench.toml /tmp/branch-bench-sample/
cd /tmp/branch-bench-sample
branch-bench run
branch-bench report
branch-bench show
```

Or from the examples dir directly (bench.toml already points at `/tmp/branch-bench-sample`):

```bash
cd examples/java-maven-jmh
branch-bench run
branch-bench report
branch-bench show
```

### 3. Push to GitHub (optional)

```bash
cd /tmp/branch-bench-sample
git remote add origin git@github.com:YOU/branch-bench-sample.git
git push -u origin perf-improvements
```

## Prerequisites

- Java 21+
- Maven 3.8+
- branch-bench installed (`pip install branch-bench` or `pipx install branch-bench`)

## Adding more commits

Add a new directory under `commits/` with only the changed files, then add its
commit message to `MESSAGES` in `generate-repo.sh` and re-run the script.
