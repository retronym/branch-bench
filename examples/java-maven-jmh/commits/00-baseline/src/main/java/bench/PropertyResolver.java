package bench;

import java.util.Map;
import java.util.regex.Pattern;

/**
 * Resolves ${key} placeholders in strings by looking up values in a property map.
 *
 * Baseline: compiles a new Pattern for every property on every call.
 */
public class PropertyResolver {

    private final Map<String, String> properties;

    public PropertyResolver(Map<String, String> properties) {
        this.properties = properties;
    }

    public String resolve(String input) {
        String result = input;
        for (Map.Entry<String, String> entry : properties.entrySet()) {
            Pattern pattern = Pattern.compile("\\$\\{" + Pattern.quote(entry.getKey()) + "\\}");
            result = pattern.matcher(result).replaceAll(entry.getValue());
        }
        return result;
    }
}
