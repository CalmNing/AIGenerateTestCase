---
name: testcase-design
description: >
  Core test case design methodology for software testing. Use when the user asks you to generate, design,
  or create test cases from requirements. Provides a structured three-step workflow: requirements analysis,
  test case design across four levels, and quality review.
license: MIT
allowed-tools: get_existing_coverage
---

# Test Case Design

## When to Use

- User provides software requirements and asks you to generate test cases
- User asks you to design test coverage for a feature, module, or system
- User requests test case creation, test planning, or test design
- Any task involving converting requirements into structured test cases

## Three-Step Workflow

You must strictly follow this three-step process:

### Step 1: Requirements Analysis

Before designing test cases, deeply analyze the requirement:
- Extract core functionality points and business rules
- Identify input boundaries (min, max, null, special characters)
- Map out exception paths and error scenarios (network failures, concurrency conflicts, data inconsistency)
- If session context is provided, analyze interactions and data flow between this requirement and existing modules
- If existing test coverage data is available via `get_existing_coverage`, identify coverage gaps

### Step 2: Test Case Design

Design test cases across four levels based on your analysis:

| Level | Name | Design Method | Suggested Count |
|-------|------|---------------|-----------------|
| 1 | Functional | Equivalence partitioning: select representatives of valid equivalence classes, cover all normal paths | 2-4 |
| 2 | Boundary | Boundary value analysis: test boundary points, boundary+1, boundary-1 for values/lengths/collections | 2-5 |
| 3 | Exception | Error guessing: invalid inputs, null values, overflow, special chars, concurrency conflicts, dependency failures | 3-6 |
| 4 | Scenario | Scenario/flowchart method: end-to-end user workflows, basic and alternative flows, cross-module integration | 2-5 |

### Step 3: Quality Review

Self-check each case before output:
- Is the case name clear and descriptive (no IDs like TC001)?
- Are preconditions specific, non-empty, and reproducible?
- Are steps ordered, concrete, and actionable?
- Are expected results explicit and quantifiably verifiable?
- Are all four levels covered (adjusted for requirement complexity)?
- Do scenario cases include cross-module interactions when context is available?
- Avoid duplication with existing test cases?

## Design Principles

- **Executability first**: Every case must be ready to execute. Avoid vague descriptions like "check if system is normal"
- **Concrete data**: Use specific test data (e.g., "input phone 13800138000" not "input a valid phone number")
- **Cross-module awareness**: When session context is available, level 4 scenario cases must include at least one end-to-end flow that interacts with existing modules
- **Right-sizing**: Typical requirements need 10-15 cases; complex ones (multiple sub-features) need 15-25. Do not pad.

## Constraints

- Case names must NOT contain case IDs (TC001, case1, etc.)
- Case names must not be empty
- Preconditions must not be empty
- Each case must state which design method was used (in the design_method field)

## Output Format

Output a list of test cases with these fields:
- `case_name`: Clear descriptive name
- `steps`: Ordered list of test steps (concrete and actionable)
- `preset_conditions`: List of preconditions (specific and reproducible)
- `expected_results`: List of expected outcomes (explicit and verifiable)
- `case_level`: 1 (functional), 2 (boundary), 3 (exception), or 4 (scenario)
- `design_method`: The test design method used
