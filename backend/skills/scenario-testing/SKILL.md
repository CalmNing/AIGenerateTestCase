---
name: scenario-testing
description: >
  End-to-end scenario and workflow-based testing methodology. Use when designing
  test cases that simulate real user journeys, multi-step business processes,
  or cross-module integration flows.
license: MIT
---

# Scenario Testing

## When to Use

- Requirements describe user-facing workflows or business processes
- System has multiple interacting modules or components
- Session context provides existing modules for cross-module integration testing
- Need to validate end-to-end data flow between subsystems
- Real-world user journey simulation is required

## Scenario/Flowchart Method

### Identifying Scenarios

1. **Basic Flow (Happy Path)**: The most common, error-free path through the system
2. **Alternative Flows**: Valid variations of the basic flow (e.g., different payment methods)
3. **Exception Flows**: Paths that handle errors and return to a valid state
4. **Integration Flows**: Paths that span multiple modules or subsystems

### Scenario Design Process

#### Step 1: Map the Flow

Draw the user journey from entry to exit:
```
Start → Step A → Decision Point → Step B/C → Step D → End
```

#### Step 2: Identify All Paths

- Basic flow: The primary path from start to successful completion
- Alternative paths: Different valid routes through decision points
- Error paths: What happens at each step when something fails

#### Step 3: Design Test Cases

For each path, define:
- **Preconditions**: System state before the scenario begins
- **Trigger**: What initiates the scenario
- **Sequence**: Ordered steps through the flow
- **Checkpoints**: Verifiable states at key decision points
- **Final state**: Expected system state after completion

### Cross-Module Integration

When session context provides existing modules, design scenarios that:

1. **Chain module operations**: Use output from one module as input to another
   - Example: Create a record in Module A → verify it appears in Module B's dropdown

2. **Test data propagation**: Verify data consistency across modules
   - Example: Update a field in Module A → verify Module B reflects the change

3. **Test combined workflows**: Simulate real user workflows spanning multiple modules
   - Example: Register (Auth) → Create profile (User Management) → Submit order (Order System)

4. **Test module dependencies**: Verify correct behavior when dependent module data changes
   - Example: Delete a referenced record → verify Module B handles the missing reference

### Design Pattern

```
Case name: "End-to-end: [user goal] via [path description]"
Preconditions:
  - Initial system state
  - Required test data setup
Steps:
  1. [First action]
  2. [Second action / decision]
  3. [Verification checkpoint]
  ...
Expected Results:
  - [State verification]
  - [Data consistency check]
  - [UI/UX confirmation]
```

## Quality Checklist

- Does the scenario represent a realistic user goal?
- Are all decision points covered by at least one path?
- Is the sequence of steps in a logical, time-ordered sequence?
- Are checkpoints at natural verification points?
- Do cross-module scenarios test real integration, not just sequential calls?
- Is the final state clearly defined and verifiable?
