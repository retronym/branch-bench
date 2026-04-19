package bench;

import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * Benchmarks the "no placeholders" path: inputs that contain no ${...} markers.
 *
 * Highlights the cost of the scanning strategy when there is nothing to replace.
 * The regex baseline re-compiles a Pattern for every property even though none match.
 * The single-pass implementations pay only a linear scan cost.
 */
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.NANOSECONDS)
@State(Scope.Benchmark)
@Warmup(iterations = 3, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(1)
public class PlainTextBenchmark {

    private PropertyResolver resolver;

    /** Pre-resolved strings — identical content to PropertyResolverBenchmark templates
     *  but with all placeholders already substituted, so nothing needs replacing. */
    private static final String[] PLAIN_STRINGS = {
        "Hello, alice! Welcome to my-service.",
        "Version 3.1.4 running on prod.example.com (build 1729)",
        "User: alice, Project: my-service, Version: 3.1.4",
        "prod.example.com:8080/my-service/api/v1729",
        "Deploying my-service 3.1.4 to prod.example.com as alice",
        "Build 1729: my-service 3.1.4 by alice on prod.example.com",
    };

    @Setup
    public void setup() {
        Map<String, String> props = new LinkedHashMap<>();
        props.put("user.name", "alice");
        props.put("app.version", "3.1.4");
        props.put("env.host", "prod.example.com");
        props.put("build.number", "1729");
        props.put("project.name", "my-service");
        resolver = new PropertyResolver(props);
    }

    @Benchmark
    public void resolveNoMatch(Blackhole bh) {
        for (String s : PLAIN_STRINGS) {
            bh.consume(resolver.resolve(s));
        }
    }
}
