#!/usr/bin/env bash
# Crosscheck installer: one command on a Linux machine with KVM, or on a Proxmox VE host.
#
#   curl -fsSL https://raw.githubusercontent.com/ProfessorEngineergit/Crosscheck/main/install.sh | sudo bash
#
# Options are passed to `crosscheck init`, for example:
#   ... | sudo bash -s -- --role runner
#   ... | sudo bash -s -- --yes --repo me/app --github-token-file /root/gh.token
#
# Environment: CROSSCHECK_REF (git ref to install, default main), CROSSCHECK_PREFIX (default /opt/crosscheck).
# The script only installs packages, a Python virtualenv and a symlink, then hands over to `crosscheck init`.
# Read it before running it; it is short on purpose.
set -euo pipefail

REF="${CROSSCHECK_REF:-main}"
REPO_URL="https://github.com/ProfessorEngineergit/Crosscheck"
PREFIX="${CROSSCHECK_PREFIX:-/opt/crosscheck}"

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "please run as root (sudo)"
[ "$(uname -s)" = "Linux" ] || die "Crosscheck runs on Linux"
[ "$(uname -m)" = "x86_64" ] || warn "only x86_64 hosts are tested"
[ -r /etc/os-release ] || die "cannot detect the distribution"
. /etc/os-release

is_proxmox=0
command -v pveversion >/dev/null 2>&1 && is_proxmox=1

log "installing system packages"
case " ${ID:-} ${ID_LIKE:-} " in
  *" debian "*|*" ubuntu "*)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    pkgs="python3 python3-venv git curl ca-certificates iproute2 openssh-client"
    [ "$is_proxmox" -eq 1 ] || pkgs="$pkgs qemu-system-x86 qemu-utils"
    # shellcheck disable=SC2086
    apt-get install -y -q $pkgs
    ;;
  *" fedora "*|*" rhel "*|*" centos "*)
    dnf install -y -q python3 git curl iproute openssh-clients qemu-kvm qemu-img
    ;;
  *) die "unsupported distribution: ${ID:-unknown} (Debian, Ubuntu, Proxmox VE and Fedora are supported)" ;;
esac

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 or newer is required (found $(python3 --version 2>&1))"

log "installing Crosscheck ($REF) into $PREFIX"
python3 -m venv "$PREFIX"
"$PREFIX/bin/pip" install -q --upgrade pip
"$PREFIX/bin/pip" install -q --upgrade "crosscheck @ git+${REPO_URL}@${REF}"
ln -sf "$PREFIX/bin/crosscheck" /usr/local/bin/crosscheck

if [ -w /sys/kernel/mm/ksm/run ]; then
  log "disabling KSM (memory deduplication is a side channel between VMs)"
  echo 2 > /sys/kernel/mm/ksm/run || true
  systemctl disable --now ksmtuned >/dev/null 2>&1 || true
fi
[ -e /dev/kvm ] || warn "/dev/kvm is missing: enable virtualisation (VT-x/AMD-V) in the firmware settings"

log "starting guided setup"
# With `curl | bash`, stdin is the script itself: reattach the terminal if there is one.
if [ -t 0 ]; then
  exec crosscheck init "$@"
elif (exec < /dev/tty) 2>/dev/null; then
  exec crosscheck init "$@" < /dev/tty
else
  exec crosscheck init "$@"
fi
