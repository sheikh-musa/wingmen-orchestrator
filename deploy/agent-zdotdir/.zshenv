# Minimal agent ZDOTDIR .zshenv (op#29348 round 3, 2026-10-26, bus #77152)
#
# This is a FILTERED, tracked copy of the operator's real ~/.zshenv + ~/.zshrc,
# carrying forward only the non-secret bits agent-lane boot scripts actually
# rely on (PATH additions, JAVA_HOME/ANDROID_HOME toolchain setup). It
# deliberately DROPS 4 export lines that exist in the real dotfiles:
#   GH_TOKEN (x2, one in each real file), GOOGLE_APPLICATION_CREDENTIALS (x2),
#   CLAUDE_CODE_OAUTH_TOKEN.
#
# Why this file exists: PR#514 unset GOOGLE_APPLICATION_CREDENTIALS at
# boot-time in each singleton launcher, but zsh unconditionally re-sources
# ~/.zshenv for EVERY subsequent invocation (interactive or `-c`, regardless
# of parent env) -- so every individual Bash-tool command still picked the
# cosem prod SA key straight back up off disk. Setting `ZDOTDIR` to this
# directory in each boot script, BEFORE exec'ing claude, makes zsh look here
# instead of $HOME for .zshenv/.zshrc/.zprofile/.zlogin -- so agent-spawned
# shells never reach the real files at all. This does NOT touch the
# operator's own ~/.zshenv/~/.zshrc, and does NOT affect his own interactive
# terminal sessions (he doesn't go through these boot scripts).
#
# Dispositive test that isolated the mechanism (unset-first, so inheritance
# can't masquerade as re-sourcing): with GOOGLE_APPLICATION_CREDENTIALS unset
# in the parent, a `zsh -c` child with real dotfiles reachable came back
# PRESENT; the same child with ZDOTDIR pointed at an empty dir came back
# ABSENT. Carrier confirmed: ~/.zshenv re-sourcing, not stale process env,
# not a tmux session-environment table, not /etc/zshenv.
#
# MAINTENANCE: if the operator's real ~/.zshenv or ~/.zshrc gains a new
# non-secret line that a lane needs, mirror it here too -- this file is NOT
# auto-synced from the real dotfiles, it's a deliberate manual filter.
#
export PATH="$HOME/.local/bin:$PATH"

# Added by Wingmen orchestrator setup — npm global prefix for pm2
export PATH="$HOME/.npm-global/bin:$PATH"

# op#27773 Android toolchain (Temurin JDK 21, no-sudo direct install)
export JAVA_HOME="$HOME/android-toolchain/jdk-21.0.12.1+1/Contents/Home"
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="/usr/local/share/android-commandlinetools"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
