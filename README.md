# Clawp

AI assistant and agent framework written in Python (and if we call it in the
diminutive "Clawpy" we got the "py" for Python, so there you go).

## Copyright and license

Copyright 2026 Marc Lehmann

This file is part of clawp.

clawp is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

clawp is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along
with clawp. If not, see <https://www.gnu.org/licenses/>.

## Gateway

The gateway is the backend component of the system. It runs the agents and
provides a REST API to interact with them.

### Installing and running

#### Docker

Use the `docker-compose.yaml` to, which expects a `config.yaml` in the root
directory and mounts `clawp_files/` for data:

```
docker compose up --build
```

Run tests with the `docker-compose.test.yaml`

```
docker compose -f docker-compose.test.yaml up --build --exit-code-from=test
```

#### Local

Clawp needs external dependencies:

- `uv`
- `libolm` (for Matrix encryption)
- the `rust-mcp-filesystem` MCP server, which can be install with
  `cargo install --locked rust-mcp-filesystem@0.4.1`

Then install via

```
uv sync --frozen
```

Available extras (using `--extra`):

- `test`: dependencies to run tests
- `dev`: dependencies for development

From the root directory, run Clawp with

```
uv --project gateway run clawp
```

Run tests with

```
uv --project gateway run pytest gateway
```

### Configuration

Clawp is mostly configured via a configuration file. See `config.yaml.example`.
Some values (secrets) are specified as references to environment variables.

### Tools

#### Shell sandbox

The gateway includes a tool that lets agents run shell commands. Due to the
destructive potential, commands are meant to be run in a sandbox (though the
gateway can be configured to run on the same host). The shell tool connects to
the sandbox via SSH and then executes a single command. Every tool call starts a
new shell, so working directory and environment doesn't persist. On the host
running the sandbox (which may be the same host as the gateway), the base
directory containing files must be accessible at the same path (e.g. a volume
mounted to the same mountpoint). The shell tool expects a `command_wrapper.bash`
script in path, which ensures permission boundaries before running the command.

This is how the sandbox is implemented in the `docker-compose.yaml`:

- A sandbox container is started, which starts an SSH server. An init container
  creates a key pair in a volume accessed by gateway and sandbox, so the gateway
  can connect to the sandbox.
- Both gateway and sandbox mount the `./clawp_files` directory.
- Both the gateway and sandbox create a user and group clawp:clawp with the same
  uid/gid (2000:2000).
- The wrapper script inside the sandbox creates a system user just for the agent
  and ensures it has access to its workspace directory and nothing else. It also
  makes sure the gateway always has access to the workspace via the clawp group
  (see the [wrapper script](shell_sandbox/command_wrapper.bash)'s documentation
  for details).
- When starting the sandbox, an init container goes through all existing agents
  in the agents directory, creates a system user for each one and sets up all
  the permissions. This is necessary since uids/gids are assigned on a
  first-come first-serve basis, so agents' workspaces may belong to a uid from a
  previous run, which is now assigned to another agent. Going through everything
  on startup ensures no agent can access other agents' files by accident.
- The umask for the gateway process is set to 0007, which means any files and
  directories created by the gateway itself are not readable by others (which
  would make them accessible to the agents).

The sandbox' Dockerfile has an `EXTRA_PACKAGES` argument that can be populated
with a space-separated list of extra packages to install (via `apt-get` on an
Ubuntu image).
