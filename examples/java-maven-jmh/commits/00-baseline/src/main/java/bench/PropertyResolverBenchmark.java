package bench;

import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MICROSECONDS)
@State(Scope.Benchmark)
@Warmup(iterations = 3, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(1)
public class PropertyResolverBenchmark {

    private PropertyResolver resolver;

    private static final String[] TEMPLATES = {
        "Hello, ${user.name}! Welcome to ${project.name}.",
        "Version ${app.version} running on ${env.host} (build ${build.number})",
        "User: ${user.name}, Project: ${project.name}, Version: ${app.version}",
        "${env.host}:8080/${project.name}/api/v${build.number}",
        "Deploying ${project.name} ${app.version} to ${env.host} as ${user.name}",
        "Build ${build.number}: ${project.name} ${app.version} by ${user.name} on ${env.host}",
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
    public void resolveTemplates(Blackhole bh) {
        for (String template : TEMPLATES) {
            bh.consume(resolver.resolve(template));
        }
    }
}
