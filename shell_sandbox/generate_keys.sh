#!/bin/sh

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

# This script generates an SSH key pair named id_shell_sandbox{,.pub} and places
# it in /shared_ssh. It sets the owner of the private key to clawp:clawp, which
# is the user and group used inside the gateway container. It also copies the
# public key to the filed authorized_keys in the same directory, so it can be
# mounted directly.

set -e

KEY_DIR="/shared_ssh"
PRIVATE_KEY="$KEY_DIR/id_shell_sandbox"
PUBLIC_KEY="$KEY_DIR/id_shell_sandbox.pub"

echo "Generating fresh SSH key pair for shell sandbox."
yes | ssh-keygen -t ed25519 -N "" -f "$PRIVATE_KEY" -q -C "auto-generated key"
chmod 600 "$PRIVATE_KEY"
# Set private key owner to clawp:clawp, which is the user ID in the gateway.
chown clawp:clawp "$PRIVATE_KEY"

echo "Preparing authorized_keys file."
cp "$PUBLIC_KEY" "$KEY_DIR/authorized_keys"
chmod 600 "$KEY_DIR/authorized_keys"

echo "SSH keys initialized successfully."
