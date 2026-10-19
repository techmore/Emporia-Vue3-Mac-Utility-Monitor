#!/bin/zsh
set -eu
script_dir=${0:A:h}
/opt/homebrew/opt/energy-monitor/libexec/venv/bin/python3 "$script_dir/capture_radon.py"
printf '\nPress Enter to close this window.'
read -r
