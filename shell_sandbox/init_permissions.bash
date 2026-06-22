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

# This script sets up system users for all agents it finds in the given base
# directory and ensures directory permissions. It is meant to be run on sandbox
# startup where none of the users exist yet, to make sure none of the existing
# permissions accidentally give an agent permissions they're not meant to have.
# See the documentation of command_wrapper.bash for an explanation of the
# permissions system in the sandbox.
#
# Usage: init_permissions.bash <clawp_base_dir>

set -e

source /scripts/lib/command_lib.bash

if [ "$#" -ne 1 ]; then
    echo "Usage error in the permission init script." >&2
    exit 1
fi

CLAWP_BASE_DIR="$1"

# Go through the agents directory and create users for all of them. This also
# sets agent-specific permissions. If a user exists, set the permissions.
for AGENT_ID in $(ls "$CLAWP_BASE_DIR/agents") ; do
    if ! id "$AGENT_ID" &>/dev/null; then
        if ! create_agent_user "$CLAWP_BASE_DIR" "$AGENT_ID" ; then
            echo "Error creating user $AGENT_ID." >&2
            exit 1
        fi
    else
        if ! ensure_agent_dir_permissions "$CLAWP_BASE_DIR" "$AGENT_ID" ; then
            echo "Error setting permissions for $AGENT_ID." >&2
            exit 1
        fi
    fi
done

# Finally, ensure the base directories leading up to the agent's base directory
# have ownership and permissions set correctly.
if ! ensure_base_dir_permissions "$CLAWP_BASE_DIR" ; then
    echo "Error setting base directory permissions." >&2
    exit 1
fi
