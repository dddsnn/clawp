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

# This script contains bash functions that can be used to manage agent users and
# file/directory permissions in the sandbox. All functions assume a directory
# structure where agentss workspaces are at
# <clawp_base_dir>/agents/<agent_id>/workspace. Further, there must be a system
# user/group named clawp:clawp which belongs to the gateway and which is
# supposed to have access to all agents' workspaces.
#
# For the reasoning behind these choices, see the documentation of
# command_wrapper.bash.

agent_base_dir() {
    CLAWP_BASE_DIR="$1"
    AGENT_ID="$2"
    echo "${CLAWP_BASE_DIR}/agents/${AGENT_ID}"
}

agent_workspace_dir() {
    CLAWP_BASE_DIR="$1"
    AGENT_ID="$2"
    echo "$(agent_base_dir $CLAWP_BASE_DIR $AGENT_ID)/workspace"
}

clawp_user_group_exists() {
    if ! id clawp &>/dev/null; then
        return 1
    elif ! getent group clawp &>/dev/null ; then
        return 1
    fi
    return 0
}

# Ensure correct permissions on the Clawp base directories.
# Usage: ensure_base_dir_permissions <clawp_base_dir>
#
# Sets ownership of the parent directories of the agent base directories to
# clawp_clawp and their mod to 711, making it possible for agent users to cd
# through them, but not access anything inside. The directories must exist.
ensure_base_dir_permissions() {
    CLAWP_BASE_DIR="$1"
    AGENTS_DIR="$CLAWP_BASE_DIR/agents"

    if [ ! -d "$AGENTS_DIR" ]; then
        echo "$AGENTS_DIR does not exist." >&2
        return 1
    fi
    if ! clawp_user_group_exists ; then
        echo "User clawp:clawp does not exist." >&2
        return 1
    fi

    # Now the the agent user exists, set permissions of the directories leading up
    # to the agent's workspace (owned by clawp:clawp) to 711 so the agent can cd
    # through them, but not access their content.
    for d in "$CLAWP_BASE_DIR" "$AGENTS_DIR" ; do
        chown clawp:clawp "$d" && chmod 711 "$d"
    done
}

# Ensure the agent-specific directories have correct permissions.
# Usage: ensure_agent_dir_permissions <clawp_base_dir> <agent_id>
#
# Ensures the agent's base directory belongs to clawp:clawp with mod 711 (making
# it only accessible to the gateway, not the agent), and the agent's workspace
# belongs (recursively) to <agent_id>:clawp (making it accessible to both agent
# and gateway). Sets the sgid bit on the agent's workspace. The directories and
# the user must exist.
ensure_agent_dir_permissions() {
    CLAWP_BASE_DIR="$1"
    AGENT_ID="$2"
    AGENT_BASE_DIR="$(agent_base_dir "$CLAWP_BASE_DIR" "$AGENT_ID")"
    AGENT_WORKSPACE_DIR="$(agent_workspace_dir "$CLAWP_BASE_DIR" "$AGENT_ID")"

    if [ ! -d "$AGENT_WORKSPACE_DIR" ]; then
        echo "$AGENT_WORKSPACE_DIR does not exist." >&2
        return 1
    fi
    if ! clawp_user_group_exists ; then
        echo "User clawp:clawp does not exist." >&2
        return 1
    fi
    if ! id "$AGENT_ID" &>/dev/null; then
        echo "User $AGENT_ID does not exist." >&2
        return 1
    fi

    # The agent base directory belongs to the gateway, the agent shouldn't have
    # access to it.
    chown clawp:clawp "$AGENT_BASE_DIR" && chmod 711 "$AGENT_BASE_DIR"
    # Set ownership of the agent's workspace to the new user, but group
    # ownership to the clawp group of the gateway so the gateway can access
    # everything as well.
    chown -R "$AGENT_ID:clawp" "$AGENT_WORKSPACE_DIR"
    # Set file mod to exclude access from others, which ensures other agents
    # can't access this agent's files. We also set the sgid bit, which means any
    # directories and files the agent creates will inherit group ownership of
    # the clawp group (so the gateway also has access to them).
    chmod 2770 "$AGENT_WORKSPACE_DIR"
}

# Create a system user for an agent.
# Usage: create_agent_user <clawp_base_dir> <agent_id> <agent_shell>
#
# The user must not yet exist, but the workspace directory must. The agent's ID
# is used as the username. The agent's workspace is used as HOME. Also ensures
# the new agent's directory has the correct permissions.
create_agent_user() {
    CLAWP_BASE_DIR="$1"
    AGENT_ID="$2"
    AGENT_SHELL="$3"
    AGENT_BASE_DIR="$(agent_base_dir "$CLAWP_BASE_DIR" "$AGENT_ID")"
    AGENT_WORKSPACE_DIR="$(agent_workspace_dir "$CLAWP_BASE_DIR" "$AGENT_ID")"

    useradd --home-dir "$AGENT_WORKSPACE_DIR" --no-create-home \
        --shell "$AGENT_SHELL" --user-group "$AGENT_ID"
    if [ $? -ne 0 ] ; then
        echo "Error creating user $AGENT_ID." >&2
        return 1
    fi
    ensure_agent_dir_permissions "$CLAWP_BASE_DIR" "$AGENT_ID"
    return $?
}
