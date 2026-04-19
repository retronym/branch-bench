package bench;

import java.util.Map;

/**
 * Resolves ${key} placeholders in strings by looking up values in a property map.
 *
 * Refactor: simplified from StringBuilder to plain string concatenation for
 * readability — the JIT should optimise this anyway.
 *
 * NOTE: This is an intentional regression example. String += in a loop is O(n²)
 * because each concatenation allocates a new String copying all prior characters.
 * The JIT does NOT reliably collapse it back to a StringBuilder across loop iterations.
 */
public class PropertyResolver {

    private final Map<String, String> properties;

    public PropertyResolver(Map<String, String> properties) {
        this.properties = properties;
    }

    public String resolve(String input) {
        String result = "";
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
                        result += value;
                        i = end + 1;
                        continue;
                    }
                }
            }
            result += input.charAt(i++);
        }
        return result;
    }
}
