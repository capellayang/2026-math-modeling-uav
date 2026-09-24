# Q1–Q4 source-to-code audit, experiment branch

Source precedence: original DOCX and attachments, then TASK.md and repository docs.
The DOCX XML contains 72 `m:oMath` objects and 12 math paragraphs. Its P053–P055
specify only `E_total = E_horizontal + E_up`; no missing component relationship was
recovered. The existing horizontal and climb formulas remain explicit project
assumptions. They are dimensionally consistent but cannot be called official.

The physical resource error in the prior Q3 *optimizer* was equating six energy
components with at most six lifetime relay sorties and assigning a never-reused
component to every sortie. P062–P065 allow reuse after charging to 100%.
The prior independent Q3 validator already checked component intervals through
charge completion, so a validator PASS did not reveal the search restriction.
The experiment branch models component assignment and charge-end NoOverlap.

The zero-tardiness mode is a deliberate lexicographic search restriction. It
becomes incorrect if presented as a source hard deadline for ordinary cargo.
The experiment's Pareto mode keeps only medical and first-batch deadlines hard.
The existing local hover rectangle, maximum-altitude coarse search and scalar
route archive are finite algorithm restrictions, not source constraints.

Q4's strict relay ownership, full replication, workload sum, resource-gap
scalarization and lexicographic representative are documented project choices.
The exact partition enumeration itself is appropriate for the current scale.
Comparison experiments are under `outputs/experiments/q3_q4_improvement/`.
No formal Q1–Q4 result is replaced on this branch.
Quantitative experiments and validator statuses are in
`outputs/experiments/q3_q4_improvement/audit_report.md`.
