# Session — 2026-04-19

## Work completed

### 1. Sample project: `examples/java-maven-jmh`

Full Java + Maven + JMH reference project for branch-bench.  
Generates a real pushable git repo (`perf-improvements` branch) via `generate-repo.sh`.

#### Commit journey (5 commits)

| Dir | Commit message | Change | Expected perf |
|-----|---------------|--------|---------------|
| `00-baseline` | Integrate benchmarks: baseline property resolution using regex | `Pattern.compile` per property per call | slow ~5 µs |
| `01-stringbuilder` | Optimize resolver: StringBuilder with manual char scanning | Single-pass O(n) scan, no regex | ~10× faster |
| `02-chararray` | Micro-opt: scan over char[] to reduce charAt() overhead | `toCharArray()` upfront, avoids per-char bounds check | marginal gain |
| `03-comment` | Docs: add complexity comment to resolve() — no logic change | Comment only — **intentional no-op** | flat |
| `04-concat-regression` | Refactor: simplify resolve() to plain string concatenation | `String +=` in loop → O(n²) allocations | clear regression |

Each numbered directory contains **only the files that changed** in that commit.  
`generate-repo.sh` replays them onto a fresh repo with `cp -r dir/. $DEST/ && git commit`.

#### Files created / modified

```
examples/java-maven-jmh/
├── commits/
│   ├── 00-baseline/
│   │   ├── pom.xml                                      # Maven + JMH 1.37 + Java 21 + Shade + annprocess fix
│   │   └── src/main/java/bench/
│   │       ├── PropertyResolver.java                    # regex baseline
│   │       └── PropertyResolverBenchmark.java           # JMH @Benchmark, 6 templates, 5 props
│   ├── 01-stringbuilder/src/main/java/bench/
│   │   └── PropertyResolver.java
│   ├── 02-chararray/src/main/java/bench/
│   │   └── PropertyResolver.java
│   ├── 03-comment/src/main/java/bench/
│   │   └── PropertyResolver.java
│   └── 04-concat-regression/src/main/java/bench/
│       └── PropertyResolver.java
├── generate-repo.sh                                     # creates pushable git repo
├── demo.sh                                              # one-shot: generate + run + report
├── bench.toml                                           # ready-to-use config (points at /tmp/branch-bench-sample)
└── README.md
```

#### pom.xml fixes required for Java 21 + Maven 3.9

1. **Explicit `annotationProcessorPaths`** in `maven-compiler-plugin` — newer plugin versions no longer
   auto-discover annotation processors from `provided`-scope deps; without this the JMH annotation
   processor never runs and `META-INF/BenchmarkList` is not generated.

2. **`AppendingTransformer`** entries for `META-INF/BenchmarkList` and `META-INF/CompilerHints` in
   `maven-shade-plugin` — prevents the shade step from silently overwriting these files when merging jars.

#### Integration test

`tests/test_sample.py` — 9 pytest tests:
- repo structure (`.git`, branch name, pom, 5 commits)
- commit messages in correct order (oldest-first)
- `PropertyResolver.java` at HEAD contains `StringBuilder`, not `Pattern`
- `branch_bench.git.list_commits` returns 5 commits in branch order
- tree SHAs differ between commits

### 2. Bug fix: `/tmp` → `/private/tmp` symlink on macOS

`runner.py`: `path.relative_to(Path.cwd())` raised `ValueError` when the repo lived under `/tmp`
because macOS resolves `/tmp` → `/private/tmp`.

**Fix** — added `_try_relative()` helper:

```python
def _try_relative(p: Path) -> Path:
    if not p.is_absolute():
        return p
    try:
        return p.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return p
```

Both call sites (JMH JSON path and profile SVG path) updated to use `_try_relative`.

### 3. `demo.sh` — single command to reproduce

```bash
bash examples/java-maven-jmh/demo.sh [DEST]
```

- regenerates the git repo at `DEST` (default `/tmp/branch-bench-sample`)
- rewrites paths in `bench.toml` via `sed` if `DEST` differs from default
- runs `branch-bench run` then `branch-bench report`

### 4. Observed benchmark results (one run)

```
baseline       5.41220 ± 0.0882 µs/op   (regex, recompile every call)
stringbuilder  0.84235 ± 0.0273 µs/op   (6.4× faster)
chararray      0.61538 ± 0.0193 µs/op   (1.4× over stringbuilder)
comment        0.60888 ± 0.0094 µs/op   (flat — no-op confirmed)
concat-regr    1.12734 ± 0.0187 µs/op   (1.9× regression from chararray)
```

## Pending / follow-up

- [ ] Proper release of `_try_relative` fix (currently hand-copied into pipx venv)
- [ ] Push sample repo to `github.com/YOU/branch-bench-sample` on `perf-improvements`
- [ ] Optional: add commits 05+ (e.g. precompile-regex intermediate step, parallel streams)
