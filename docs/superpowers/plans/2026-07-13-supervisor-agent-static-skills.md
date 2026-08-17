# Supervisor Agent and Static Skills Implementation Plan

1. Add failing unit tests for `SupervisorAgent` delegation, unknown steps, Agent validation, and static Skill selection.
2. Replace contextual Skill scoring with fixed ordered routes while preserving bounded, path-safe Skill loading.
3. Add `backend/app/agents/supervisor.py` with immutable delegation records and audit metadata.
4. Instantiate the Supervisor in `WorkflowEngine`, validate each branch's Agent role, and use the Supervisor name for control events.
5. Update architecture documentation to distinguish Supervisor delegation, Engine execution, Agent reasoning, Provider tools, and the unused `.agents` directory.
6. Run focused routing/workflow tests, then the complete backend test suite.
