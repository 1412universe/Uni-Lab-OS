# Invalidate CP-SAT candidates when a task pauses

Status: accepted

The CP-SAT result is a validated candidate for one planning snapshot. It is not
a durable queue and does not reserve future device execution. A Workflow Task
pause closes the dispatch gate and immediately invalidates every candidate node
that has not crossed the Node Dispatch Boundary.

An already admitted or in-flight Job keeps its original payload, execution
Claim, resource locks, and device execution. The next planning snapshot
represents that Job as fixed and preserves its resource holds. Pending nodes
remain undispatched and receive no new execution resources while the gate is
closed.

Resume creates a new planning revision from the current runtime facts. CP-SAT
may reorder the undispatched suffix to satisfy dependencies, current resource
availability, and edited entry values. A previous candidate may be supplied as
a stability hint, but it is never accepted unless its revision, time origin,
node set, and resource facts validate against the new snapshot.

The persistent pre-dispatch admission check remains the final authority. The
pause operation must make the candidate state observably stale before it
returns, while the admission check must still reject a stale candidate if a
pause races with a scheduling round. This rule covers ordinary CP-SAT Jobs;
single-step execution and error-manual handling are separate explicit control
paths.

Required regression coverage includes paused pending nodes being absent from
the CP-SAT model, in-flight nodes remaining fixed with their holds, stale
candidate rejection after a pause, and re-planning of the pending suffix after
resume.
