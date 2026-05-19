---
name: coverage-analysis
description: >
  Analyze existing test coverage to identify gaps and avoid duplicate test cases.
  Use the get_existing_coverage tool to understand what's already covered, then
  design complementary test cases that fill the gaps.
license: MIT
allowed-tools: get_existing_coverage
---

# Coverage Analysis

## When to Use

- Before generating new test cases for a module
- When you have access to the `get_existing_coverage` tool
- When you need to avoid duplicating existing test cases
- When filling test coverage gaps is important

## Workflow

### Step 1: Query Existing Coverage

Call `get_existing_coverage` to retrieve:
- Existing test case names grouped by level (1-4)
- Which levels already have coverage
- Which levels are missing coverage

### Step 2: Analyze Gaps

Review the coverage data:
- **Missing levels**: Prioritize creating cases for uncovered levels
- **Sparse coverage**: If a level has only 1-2 cases, look for additional scenarios
- **Duplicate risk**: Review existing case names to avoid designing similar cases

### Step 3: Design Complementary Cases

Based on the gap analysis:
1. First fill completely missing levels
2. Then strengthen sparse levels
3. Finally add novel scenarios that existing cases don't cover

### Step 4: Verify Non-Duplication

Before finalizing, review your designed case names against the existing list to ensure they test different aspects.

## Coverage Level Reference

- **Level 1 (Functional)**: Normal operation, valid inputs, happy paths
- **Level 2 (Boundary)**: Edge values, limits, boundaries
- **Level 3 (Exception)**: Errors, invalid inputs, failures
- **Level 4 (Scenario)**: End-to-end workflows, cross-module integration

## Key Principle

Avoid designing test cases that duplicate existing coverage. Focus on filling gaps and strengthening weak areas. If all four levels are well-covered, design novel scenarios that exercise untested combinations or edge cases.
