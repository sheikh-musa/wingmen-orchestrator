"""cc-fleet-health arm-gating for the SRE's OWN liveness watchdog (Nazim #31259).

The wedge-staged verdict false-fires on cc-fleet-health's own idle DIM autosuggestion
ghost (composer_capture reports ph_basis='real-text(dim)'); each false-fire self-nudges
the SRE for nothing. `_is_cosmetic_dim_idle` gates that self-wake: suppress ONLY when the
composer is dim AND nothing is piling in the inbox. A NOT-dim composer (possible real stuck
— the ENOSPC composer-corruption class) or a piling inbox is never suppressed. This never
trusts dim to mean empty — a real dim wedge that matters surfaces when work piles.
"""
import scripts.sre_liveness_watchdog as s


def test_dim_ghost_with_nothing_piling_is_cosmetic_suppressed():
    assert s._is_cosmetic_dim_idle("real-text(dim)", piling=False) is True
    assert s._is_cosmetic_dim_idle("real-text(dim,history-ghost)", piling=False) is True
    assert s._is_cosmetic_dim_idle("dim-sgr", piling=False) is True


def test_dim_ghost_but_inbox_piling_is_NOT_suppressed():
    # dim composer + piling unread = genuinely not draining -> still a wedge worth a nudge.
    assert s._is_cosmetic_dim_idle("real-text(dim)", piling=True) is False


def test_not_dim_is_never_suppressed_even_idle():
    # NOT-dim = possible real stuck text (ENOSPC composer-corruption). Must still nudge/escalate.
    # Critical: 'not-dim' contains the substring 'dim' — the gate must exclude it.
    assert s._is_cosmetic_dim_idle("real-text(not-dim)", piling=False) is False
    assert s._is_cosmetic_dim_idle("real-text(not-dim)", piling=True) is False


def test_non_dim_bases_are_not_suppressed():
    for ph in ("no-content", "literal-fallback", "n/a", ""):
        assert s._is_cosmetic_dim_idle(ph, piling=False) is False
