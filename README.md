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

### Channels

#### Github

The github channel contains chats that represent Github issues and PRs for
agents to interact with. It gives agents access credentials to Github to use via
`git` and `gh` in the `shell` tool.

Agents are authenticated as Github apps (that's the users with a "[bot]" after
the login name). For each channel account, a separate Github app has to be
created (settings -> applications). The name of the app will be the login name
of the agent on Github. Webhooks should be disabled (Clawp uses polling via the
REST API). The app needs at least these repository permissions to work:

- contents (read-write)
- metadata (read-only)
- issues (read-write)
- pull requests (read-write)

The app then needs to be installed into an organization to gain access to one or
more repositories. It might make sense to create an organization just for Clawp
agents.

The app needs to be configured as a github channel in Clawp by specifying

- `app_id`: Shown in the app settings.
- `installation_id`: This identifies the installation of the app in the
  organization and is (annoyingly) not displayed explicitly. However, it's part
  of the URL of the app configuration page (org settings -> Github apps ->
  configure):
  `/organizations/<org_name>/settings/installations/<installation_id>`
- `private_key`: `.pem` file that acts as credentials. Created in the app
  settings.
- `organization`: Name of the organization where the app is installed.
- `agent_email`: Email address for the agent to use for commits. The agent
  doesn't need to receive/send emails, it's just used to configure git's
  user.email.

Github apps don't have quite the same features as regular users. Crucially, they
can't be assigned an issue. To work around this, Clawp uses special labels of
the format `agent-assigned:<agent_login>`, where `agent_login` is the agent's
login name including the "[bot]" suffix. E.g., if the app is called
"clawp-agent-avery", the label would be "agent-assigned:clawp-agent-avery[bot]".
This label needs to be manually created in each repository or once in the
organization. Clawp treats the existence of this label on an issue like
assignment and informs the agent of any updates.

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

Security note: The wrapper script uses `runuser` with the `-T` flag to not
allocate a pseudo-terminal. This strips conole formatting characters and makes
the output easier to understand. However, it also enables privilege escalation
via TIOCSTI/TIOCLINUX ioctl command injection if this legacy feature is set in
the kernel. To run safely, `CONFIG_LEGACY_TIOCSTI` must be unset.

The sandbox' Dockerfile has an `EXTRA_PACKAGES` argument that can be populated
with a space-separated list of extra packages to install (via `apt-get` on an
Ubuntu image).
