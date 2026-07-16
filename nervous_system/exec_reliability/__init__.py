"""Execution-Reliability Layer (op#4711 / CAI-RESP-464).

Separates DECIDING from EXECUTING: a GRANTED strategic decision drains onto a
durable exec_work_item that a disposable, stateless runner claims and executes.
The runner is a pure post-gate executor — it borrows authority from the grant,
acts under its own agent_id, fails closed, and never decides.

AUTHORED-UNAPPLIED: the migration is not applied and nothing here is launchd-wired.
"""
