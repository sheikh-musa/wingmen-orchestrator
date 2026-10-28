"""provisioner — the ceayj tenant-provisioner (op#34565 option b, plan
reports/ceayj-tenant-provisioner-plan-20261027.md, gate #78463).

Modeled on the irsyad exec-broker (hc#95, scripts/lib/exec_broker) with one
deliberate divergence in the safer direction: that broker guards
wire-carried SQL; this one exposes only VERBS THAT RENDER THEIR OWN SQL —
no SQL crosses the wire at all. Generalizes scripts/provision_bariq.py
(op#34584) from one console-run carve-out to client-parameterized
self-serve-with-a-gate.
"""
