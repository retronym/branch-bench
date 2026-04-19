package bench;

import java.util.Map;

/**
 * Resolves ${key} placeholders in strings by looking up values in a property map.
 *
 * Micro-opt: convert input to char[] once upfront to avoid repeated charAt()
 * bounds-check overhead on every character access.
 *
 * Complexity: O(n * k) where n = input length, k = number of properties.
 * Each character is visited at most twice (once during scan, once copied into
 * the StringBuilder), so the constant factor is small.
 */
public class PropertyResolver {

    private final Map<String, String> properties;

    public PropertyResolver(Map<String, String> properties) {
        this.properties = properties;
    }

    public String resolve(String input) {
        char[] chars = input.toCharArray();
        StringBuilder sb = new StringBuilder(chars.length);
        int i = 0;
        while (i < chars.length) {
            if (chars[i] == '$'
                    && i + 1 < chars.length
                    && chars[i + 1] == '{') {
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
            sb.append(chars[i++]);
        }
        return sb.toString();
    }
}
