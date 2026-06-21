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
# Usage: command_wrapper.bash <clawp_base_dir> <agent_id> <shell> <cwd> <cmd>
#
# This executes <cmd> for the given <agent_id> using <shell>, changing directory
# to <cwd> first. <clawp_base_dir> is the directory containing the system's
# files. The agent's workspace is assumed to be at
# <clawp_base_dir>/agents/<agent_id>/workspace.
#
# The script further assumes there is a system user/group named clawp:clawp
# which belongs to the gateway and which must have access to all agents'
# workspaces. Before executing the command, it guarantees these things:
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

set -e

if [ "$#" -ne 5 ]; then
    echo "Usage error in the sandbox wrapper script." >&2
    exit 1
fi

CLAWP_BASE_DIR="$1"
shift
AGENT_ID="$1"
shift
AGENT_SHELL="$1"
shift
CWD="$1"
shift
COMMAND="$1"

AGENTS_DIR="$CLAWP_BASE_DIR/agents"
AGENT_BASE_DIR="$AGENTS_DIR/$AGENT_ID"
AGENT_WORKSPACE_DIR="$AGENT_BASE_DIR/workspace"

if [ ! -d "$AGENT_WORKSPACE_DIR" ]; then
    echo "Error in sandbox wrapper script: agent directory" \
    "$AGENT_WORKSPACE_DIR does not exist." >&2
    exit 1
fi

if ! id "$AGENT_ID" &>/dev/null; then
    # There is no system user with the same name as the agent's ID, so we need
    # to create one. We'll use their workspace as HOME.
    useradd --home-dir "$AGENT_WORKSPACE_DIR" --no-create-home \
        --shell "$AGENT_SHELL" --user-group "$AGENT_ID"
fi

# Now the the agent user exists, set permissions of the directories leading up
# to the agent's workspace (owned by clawp:clawp) to 711 so the agent can cd
# through them, but not access their content.
for d in "$CLAWP_BASE_DIR" "$AGENTS_DIR" "$AGENT_BASE_DIR" ; do
    chown clawp:clawp "$d" && chmod 711 "$d"
done

# Set ownership of the agent's workspace to the new user, but group ownership to
# the clawp group of the gateway so the gateway can access everything as well.
chown -R "$AGENT_ID:clawp" "$AGENT_WORKSPACE_DIR"
# Set file mod to exclude access from others, which ensures other agents can't
# access this agent's files. We also set the sgid bit, which means any
# directories and files the agent creates will inherit group ownership of the
# clawp group (so the gateway also has access to them).
chmod 2770 "$AGENT_WORKSPACE_DIR"

# Execute the command as the agent user. The -l flag ensures shell login stuff
# is taken care of (.bashrc etc.). -P creates an independent pseudo-terminal.
# Prefix with umask 0007 so any files and directories created by the command
# don't allow any access to others (except the agent user and the clawp group).
exec runuser -P -l "$AGENT_ID" -c "umask 0007 && cd $CWD && $COMMAND"
