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

## Installing and running

### Docker

Use the `docker-compose.yaml` to, which expects a `config.yaml` in the root
directory and mounts `clawp_files/` for data:

```
docker compose up --build
```

Run tests with the `docker-compose.test.yaml`

```
docker compose -f docker-compose.test.yaml up --build --exit-code-from=test
```

### Local

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

## Configuration

Clawp is mostly configured via a configuration file. See `config.yaml.example`.
Some values (secrets) can also be supplied as environment variables, these are
marked with comments in the example file.
