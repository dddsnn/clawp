#!/bin/bash

# Copyright 2026 Marc Lehmann
#
# This file is part of clawp.
#
# clawp is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawp is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with clawp. If not, see <https://www.gnu.org/licenses/>.

# This script wraps shell commands given by agents to ensure they have access to
# their workspace, they don't access to anything else, and the gateway isn't
# locked out either.
#
# Usage: command_wrapper.bash <clawp_base_dir> <agent_id> <envs> <cwd> <cmd>
#
# This executes <cmd> as a non-privileged user for the given <agent_id>,
# changing directory to <cwd> first. <clawp_base_dir> is the directory
# containing the system's files. The agent's workspace is assumed to be at
# <clawp_base_dir>/agents/<agent_id>/workspace.
#
# The script further assumes there is a system user/group named clawp:clawp
# which belongs to the gateway and which must have access to all agents'
# workspaces.
#
# <envs> is a comma-separated list of environment variables that should be
# preserved from the original environment when the command is run. HOME, SHELL,
# USER, and LOGNAME, shouldn't be in <envs> since they are set automatically
# (including them has no effect). PATH also shouldn't be in <envs> since it is
# always preserved.
#
# Before executing the command, these things are guaranteed:
#
# - A system user and group for the agent exist, both named <agent_id>. The
#   user's HOME is the agent's workspace.
# - All of the parent directories of the agent's workspace up to
#   <clawp_base_dir> are owned by clawp:clawp and have mode 711. This allows the
#   agent to cd through them to get to their workspace, but not access anything
#   inside.
# - The agent's workspace and its contents are owned by <agent_id>:clawp, so
#   that both the agent and the gateway (via the group) have access to the data.
# - The workspace directory has mode 2770 (i.e. no access to others, and sgid
#   bit set). This means that any files and directories created in it inherit
#   the group ownership (clawp), so the gateway can also access those.
# - The umask is set to 0007, so files and directories don't allow access from
#   others (outside of user/group).
#
# Security note: The user's privilege is dropped using runuser with the -T flag
# to not allocate a pseudo-terminal. This strips console formatting characters
# and makes the output easier to understand. However, it also enables privilege
# escalation via TIOCSTI/TIOCLINUX ioctl command injection if this legacy
# feature is set in the kernel. To run safely, CONFIG_LEGACY_TIOCSTI must be
# unset.

set -e

source /scripts/lib/command_lib.bash

if [ "$#" -ne 5 ]; then
    echo "Usage error in the sandbox wrapper script." >&2
    exit 1
fi

CLAWP_BASE_DIR="$1"
AGENT_ID="$2"
ENVS="$3"
CWD="$4"
COMMAND="$5"

if ! id "$AGENT_ID" &>/dev/null; then
    # There is no system user with the same name as the agent's ID. Create one.
    # This also sets permissions on the agent's directories.
    if ! create_agent_user "$CLAWP_BASE_DIR" "$AGENT_ID" ; then
        echo "Error creating user $AGENT_ID." >&2
        exit 1
    fi
else
    # If the user exists, we still set the permissions of their directories to
    # make sure everything is in order.
    if ! ensure_agent_dir_permissions "$CLAWP_BASE_DIR" "$AGENT_ID" ; then
        echo "Error setting permissions for $AGENT_ID." >&2
        exit 1
    fi
fi

# In any case, we ensure the base directories leading up to the agent's base
# directory have ownership and permissions set correctly.
if ! ensure_base_dir_permissions "$CLAWP_BASE_DIR" ; then
    echo "Error setting base directory permissions." >&2
    exit 1
fi

# Execute the command as the agent user. The -l flag ensures shell login stuff
# is taken care of (.bashrc etc.). It also sets the variables HOME, SHELL, USER,
# LOGNAME, and PATH. -T explicitly prevents a pseudo-terminal from being
# created, making the output more friendly to our text-based agents. -w
# whitelists the env variables we want to pass on. Prefix with umask 0007 so any
# files and directories created by the command don't allow any access to others
# (except the agent user and the clawp group). We have to explicitly export
# PATH, since the -l flag always sets it.
exec runuser -T -l "$AGENT_ID" -w "$ENVS" -c \
    "umask 0007 && export PATH="$PATH" && cd $CWD && $COMMAND"
