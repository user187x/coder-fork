#!/usr/bin/env bash
# Lock Down at start, for Sudo (coder_script.lockdown in ../main.tf): when an admin locked this workspace's sudo
# (Workspace page > Lock Down), the last sudoers rule denies every command before anyone can sign in (the script
# blocks login until it is done). Network is set before the workspace starts, by the pod's init container
# (lockdown.sh); a running workspace gets both from the platform service at once.
# main.tf renders this with templatefile(): workspace_id is the template's value (no shell braces here).
modes=$(cat '/etc/coder-lockdown/${workspace_id}' 2>/dev/null || echo off)
case ",$modes," in
*,sudo,*) ;;
*) exit 0 ;;
esac
printf '%s\n' "# Lock Down: an admin took sudo away in this workspace (Workspace page > Lock Down)." \
 'ALL ALL=(ALL:ALL) !ALL' | sudo -n tee /etc/sudoers.d/zz-coder-lockdown.new >/dev/null &&
 sudo -n chmod 0440 /etc/sudoers.d/zz-coder-lockdown.new &&
 sudo -n mv -f /etc/sudoers.d/zz-coder-lockdown.new /etc/sudoers.d/zz-coder-lockdown &&
 echo "Lock Down: sudo is off in this workspace (an admin locked it)."
