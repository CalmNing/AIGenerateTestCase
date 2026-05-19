---
name: boundary-testing
description: >
  Specialized boundary value analysis techniques for thorough edge case testing.
  Use when analyzing numeric inputs, string lengths, collection sizes, date ranges,
  or any parameter with defined limits.
license: MIT
---

# Boundary Testing

## When to Use

- Requirements specify numeric ranges, string lengths, array/collection sizes
- Parameters have min/max constraints or validation rules
- Date/time fields with defined ranges
- Any input with explicit or implicit boundaries

## Boundary Value Analysis Technique

### Identifying Boundaries

For each input parameter, identify:
- **Lower boundary**: Minimum valid value
- **Upper boundary**: Maximum valid value
- **Pre-boundary values**: One unit below boundaries
- **Post-boundary values**: One unit above boundaries

### Test Values

For a boundary at position B:

| Position | Value | Expected |
|----------|-------|----------|
| B - 1 | Boundary - 1 | Valid (if within valid range) |
| B | At boundary | Valid |
| B + 1 | Boundary + 1 | Invalid or Valid (depends on inclusive/exclusive) |

### Types of Boundaries

1. **Numeric boundaries**: min=0, max=100
   - Test: -1, 0, 1, 99, 100, 101

2. **String length boundaries**: min_len=3, max_len=20
   - Test: length 2, 3, 4, 19, 20, 21

3. **Collection size boundaries**: min_items=1, max_items=10
   - Test: empty, 1 item, 2 items, 9 items, 10 items, 11 items

4. **Date range boundaries**: start=2024-01-01, end=2024-12-31
   - Test: 2023-12-31, 2024-01-01, 2024-01-02, 2024-12-30, 2024-12-31, 2025-01-01

5. **Time boundaries**: Business hours 09:00-17:00
   - Test: 08:59, 09:00, 09:01, 16:59, 17:00, 17:01

### Design Pattern

For each boundary identified:
1. Name the case after the boundary being tested (e.g., "Phone number length minimum boundary")
2. Set preconditions that establish the boundary context
3. Write steps that exercise the exact boundary value
4. Specify expected results that confirm correct boundary behavior

### Common Pitfalls

- Forgetting to test empty/null when zero is minimum
- Testing only the exact boundary without boundary±1
- Not testing both lower AND upper boundaries
- Assuming inclusive/exclusive without verifying specification
