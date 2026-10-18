#!/usr/bin/zsh
set -e

# Verify interactive settings and the Debian plugin integrations.
[[ -o interactive && -o login && -o sharehistory && -o histverify ]]
[[ $HISTFILE == $HOME/.zsh_history && $HISTSIZE == 10000 && $SAVEHIST == 10000 ]]
[[ ${aliases[vim]} == nvim && ${aliases[ls]} == 'ls --color=auto' ]]
[[ ${aliases[j]} == z && ${aliases[ji]} == zi && ${aliases[gst]} == 'git status' ]]
(( $+functions[compdef] && $+functions[_zsh_autosuggest_start] ))
(( $+functions[_zsh_highlight] && $+functions[__zoxide_z] && $+functions[prompt_starship_precmd] ))
[[ $(bindkey '^R') == *fzf-history-widget* ]]
zstyle -a ':completion:*' matcher-list matchers
[[ $#matchers == 3 && $matchers[3] == 'l:|=* r:|=*' ]]
ls "$HOME" >/dev/null

# Verify the configured prompt character for success and failure.
for status_code in 0 1; do
  character=$(starship module character --status "$status_code")
  [[ $character == *'$'* ]]
done

# Verify tmux loads the user's settings on an isolated server.
if [[ ${1:-} != --no-tmux ]]; then
  trap 'tmux -L level1vault-config-test kill-server 2>/dev/null || true' EXIT
  tmux -L level1vault-config-test new-session -d -s config-test
  [[ $(tmux -L level1vault-config-test show-options -gv prefix) == C-a ]]
  [[ $(tmux -L level1vault-config-test show-options -gv history-limit) == 20000 ]]
  [[ $(tmux -L level1vault-config-test show-options -gwv mode-keys) == vi ]]
  tmux -L level1vault-config-test list-keys -T prefix C-s | grep -q synchronize-panes
fi

# Verify all installed configuration files belong to the live user.
for file in ~/.zshrc ~/.zprofile ~/.tmux.conf ~/.config/starship.toml; do
  [[ $(stat -c '%u:%g %a' "$file") == '1000:1000 644' ]]
done
print 'Live user configuration checks passed.'
