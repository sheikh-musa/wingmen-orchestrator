#!/usr/bin/env bash
# tty-resolver STUB for scripts/reset_auditor.sh's caller-identity guard
# (CAI-RESP-1442), injected via RESET_AUDITOR_TTY_STUB. A real controlling tty
# often doesn't exist under a test harness (CI has no pty), so this replaces
# `ps -o tty=` deterministically. $1=pid (ignored -- the guard's two call
# sites are told apart by $2, not by pid). $2=role (target|caller).
#
# Both default to the SAME value so every EXISTING test (which doesn't care
# about this gate) keeps simulating a legitimate target-self-call; a test that
# wants an unrelated caller overrides STUB_CALLER_TTY (or STUB_TARGET_TTY) to
# a different value.
case "${2:-}" in
  target) printf '%s' "${STUB_TARGET_TTY:-ttys000}" ;;
  caller) printf '%s' "${STUB_CALLER_TTY:-ttys000}" ;;
  *)      printf '' ;;
esac
