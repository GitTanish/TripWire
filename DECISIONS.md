# Design Decisions

## Tripwire

Tripwire is an experiment in controlling runaway execution in agentic systems. These decisions define the first implementation and the behavior its tests must verify.

### 1. Execution limits

Limits are enforced before work begins. If the iteration limit is 10, the workflow can execute 10 iterations. The eleventh never starts.

The same rule applies to tool calls. A configured budget represents the maximum permitted work, so the system must check the budget before allowing another call.

### 2. Repeated tool calls

Tripwire tracks consecutive calls to the same tool with identical inputs. Three consecutive identical calls trigger the breaker.

Inputs are normalized before comparison so equivalent dictionary inputs produce the same fingerprint. A different tool or different inputs reset the consecutive-call counter.

This is a heuristic for detecting stalled execution. It does not establish that an agent is making progress, and it may flag legitimate repeated operations.

### 3. Execution timeout

Each execution has a deadline based on a monotonic clock. When the deadline expires, Tripwire cancels the running asynchronous task and records a timeout result.

Cancellation must propagate through the execution. Operations that block the event loop or cannot cooperate with cancellation require separate isolation.

Cancellation does not reverse external side effects that have already occurred.

### 4. Result contract

Every execution terminates with a structured result. Expected limit violations are represented as results rather than exceptions.

The result records the terminal status, termination reason, iteration count, tool-call count, elapsed time, and relevant error details.

The initial statuses are `completed`, `tripped`, `failed`, and `cancelled`. Unexpected errors remain visible through the result.

### 5. Execution state

Tripwire maintains state throughout an execution, including counters, the start time, deadline, recent tool-call fingerprints, and terminal status.

Each execution has its own state. Once an execution reaches a terminal state, it cannot resume silently or reset its counters.

The initial implementation keeps state in memory. Persistence across process restarts is outside the current scope.

### 6. Implementation boundary

Tripwire owns limit enforcement, state tracking, and result generation. The execution runner owns task scheduling, tool invocation, and cancellation.

The initial implementation uses Python's asynchronous execution model and a minimal dependency set. Each decision must be covered by deterministic tests.

### 7. Verification

The implementation is considered correct when tests demonstrate that:

- Configured limits cannot be exceeded.
- Repeated identical calls trigger the expected threshold.
- Expired deadlines cancel cooperative asynchronous work.
- Every termination produces the correct structured result.
- State remains consistent throughout execution.
- Terminal executions cannot continue.

These decisions define the initial contract. Changes should follow evidence from implementation and testing.
