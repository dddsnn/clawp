# Clawp

AI assistant and agent framework written in Python (backend) and Typescript/Vue
(web UI frontent).

## Project structure

- `gateway/`: Python backend providing the infrastructure to run the agents as
  well as an API.
- `web/`: Web UI written in Typescript/Vue for chatting with the agents and
  managing them. Uses the gateway's API.

## Gateway

- Run: `uv --project gateway run clawp`
- Run tests: `uv --project gateway run pytest gateway`
