package bench;

import java.util.Map;

/**
 * Resolves ${key} placeholders in strings by looking up values in a property map.
 *
 * Optimized: single-pass scan with StringBuilder, no regex allocation.
 */
public class PropertyResolver {

    private final Map<String, String> properties;

    public PropertyResolver(Map<String, String> properties) {
        this.properties = properties;
    }

    public String resolve(String input) {
        StringBuilder sb = new StringBuilder(input.length());
        int i = 0;
        while (i < input.length()) {
            if (input.charAt(i) == '$'
                    && i + 1 < input.length()
                    && input.charAt(i + 1) == '{') {
                int end = input.indexOf('}', i + 2);
                if (end > i + 2) {
                    String key = input.substring(i + 2, end);
                    String value = properties.get(key);
                    if (value != null) {
                        sb.append(value);
                        i = end + 1;
                        continue;
                    }
                }
            }
            sb.append(input.charAt(i++));
        }
        return sb.toString();
    }
}
