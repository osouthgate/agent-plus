# Appendix A — Original Strategic Review (verbatim restore-point)

> Source: user message at session start, 2026-04-24, on branch `claude/agent-plus-strategic-plan-M4iWS`. Preserved unmodified so a future `/autoplan` re-run can rebuild the analysis from disk.

---

Perform a gstack /autoplan on this if we don't have skill load it from https://github.com/garrytan/gstack/blob/main/autoplan/SKILL.md

The plan this out and add it to the plans backlog folder

Agent-Plus: Strategic Review & Evolution Report

1. Executive Summary

Agent-Plus sits in a high-potential but rapidly commoditising space: AI agent orchestration and developer workflow augmentation.

In its current form, it is best understood as:

- A personal productivity layer
- A structured wrapper around LLM-assisted coding workflows

It delivers real utility, particularly for an experienced operator using tools like Claude Code. However, it lacks the structural characteristics required to become:

- A defensible system
- A scalable framework
- A production-grade platform

The core opportunity is not incremental improvement, but a shift in category:

«From “prompt/tool orchestration” → to “deterministic agent runtime”»

---

2. Current State Assessment

2.1 Functional Value

Agent-Plus likely provides:

- Reusable workflows
- Standardised prompts
- Task structuring
- Reduced repetition in coding tasks

This creates immediate value:

- Faster execution of known workflows
- Lower cognitive switching cost
- More consistent outputs

2.2 Structural Limitations

Despite usefulness, it likely suffers from:

1. Lack of enforcement

- Workflows are advisory, not mandatory
- Agents can skip steps or behave inconsistently

2. Stateless execution

- Each run is isolated
- No accumulation of knowledge or decisions

3. Limited observability

- Minimal insight into:
  - why decisions were made
  - how outputs evolved
  - where failures occur

4. Thin abstraction layer

- Mostly composed of:
  - prompts
  - scripts
  - light orchestration

This makes it:

«Easy to replicate, hard to scale»

---

3. Market Context

The broader ecosystem is evolving in three clear directions:

3.1 Agent Orchestration Systems

Examples:

- Multi-agent pipelines
- Role-based execution
- Structured workflows

Key characteristics:

- Step-based execution
- Defined agent responsibilities
- Pipeline enforcement

---

3.2 Agent Control Planes

Emerging pattern:

- Centralised management of agents
- Monitoring and governance
- Standardised execution environments

---

3.3 Agent Governance & Safety Layers

Increasing focus on:

- Tool access control
- Execution constraints
- Auditability

---

Key Insight

The industry is converging on:

«Structured, observable, and controllable agent systems»

Agent-Plus is aligned with this direction—but currently sits one layer too shallow.

---

4. Strategic Gap Analysis

Capability| Current State| Required State
Workflow execution| Flexible| Deterministic
State management| None / minimal| Persistent + structured
Tool usage| Open-ended| Controlled + sandboxed
Observability| Low| Full traceability
Reusability| Personal| Systemic

---

5. Evolution Path: From Tooling → Runtime

5.1 Core Transformation

The most important shift:

«Replace “guidance” with “enforcement”»

---

5.2 Minimal Agent Runtime Design

A. Workflow Engine

- Define explicit step sequences
- Enforce execution order
- Prevent skipping

Example structure:

analyze → plan → validate → execute → review

Each step:

- Has defined inputs
- Produces structured outputs
- Must pass validation before proceeding

---

B. State Layer (Critical)

Introduce persistent state:

Stores:

- Inputs
- Outputs
- Decisions
- Context history

Formats:

- JSON (structured)
- Markdown (human-readable)
- SQLite (scalable option)

Impact:

- Eliminates LLM “amnesia”
- Enables iterative refinement
- Builds long-term value

---

C. Execution Loop

Core system behaviour:

1. Load state
2. Execute next step
3. Validate output
4. Store result
5. Continue or halt

This creates:

- Repeatability
- Debuggability
- Reliability

---

D. Tool Governance Layer

Replace unrestricted execution with:

- Whitelisted tools
- Permission controls
- Optional simulation mode

Example:

Allowed:
- read_file
- write_file
- run_tests

Blocked:
- arbitrary shell execution
- network calls (optional)

---

E. Observability System

Track:

- Step inputs/outputs
- Token usage
- Execution time
- Decision rationale

Benefits:

- Debugging
- Performance tuning
- Trust in system outputs

---

6. Safety Assessment

6.1 Current Risk Profile

Likely risks:

- Arbitrary command execution
- Uncontrolled file system access
- Lack of audit trail
- No validation gates

---

6.2 Safety Classification

Environment| Safety
Local dev sandbox| Acceptable
Shared environments| Risky
Production systems| Unsafe

---

6.3 Required Improvements

To reach production-grade:

- Tool sandboxing
- Execution constraints
- Logging and audit trails
- Validation checkpoints

---

7. Measuring Real Value

7.1 Core Problem

Perceived productivity ≠ actual productivity

---

7.2 Measurement Framework

1. Time to Completion

- Baseline vs system-assisted

2. Prompt Count

- Reduction indicates abstraction value

3. Correction Rate

- Measures output quality

4. Workflow Reuse

- Indicates long-term utility

5. Cognitive Load

- Subjective but critical

---

7.3 Validation Method

Run controlled comparisons:

Metric| Without System| With System
Time| X| Y
Prompts| X| Y
Corrections| X| Y

---

7.4 Hard Test

Remove the system temporarily.

If:

- Productivity drops → system is valuable
- No change → system is overhead

---

8. Strategic Opportunities

8.1 Personal Leverage Engine

Turn Agent-Plus into:

«A system that encodes your thinking and compounds over time»

---

8.2 Lightweight Agent Runtime

Position it as:

- Simpler than enterprise systems
- More structured than prompt tooling

---

8.3 Workflow-as-Product Model

Future direction:

- Package workflows
- Reuse across contexts
- Potential distribution layer

---

9. Key Risks

9.1 Over-Engineering

- System becomes slower than direct prompting

9.2 Premature Abstraction

- Solving problems not yet validated

9.3 Maintenance Burden

- System complexity outweighs benefits

---

10. Final Verdict

Current State:

- Useful
- Intelligently designed
- Strategically aligned

Limitations:

- Not defensible
- Not scalable
- Not production-safe

---

Future Potential:

High—if evolved into:

«A deterministic, stateful, observable agent runtime»

---

11. Recommended Next Steps

Immediate (High ROI)

- Add structured workflows with enforced steps
- Introduce simple persistent state (JSON/Markdown)

---

Mid-Term

- Implement validation gates
- Add logging and traceability

---

Advanced

- Tool permission system
- Workflow versioning
- Execution replay/debugging

---

12. Closing Insight

You are not building a tool.

You are one iteration away from building:

«A system that defines how AI-assisted development actually runs»

That is a fundamentally different category—with significantly higher leverage.

---

Appendix: Decision Lens

Use this to guide future development:

- Does this reduce ambiguity or increase it?
- Does this enforce behaviour or suggest it?
- Does this compound value or reset each run?
- Does this simplify usage or add friction?

If the answer trends toward the latter, the system regresses.

If toward the former, it evolves into something meaningful.