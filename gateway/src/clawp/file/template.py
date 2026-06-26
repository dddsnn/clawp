# Copyright 2026 Marc Lehmann

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

import asyncio
import pathlib
import traceback
import typing as t

from . import base

if t.TYPE_CHECKING:
    from .. import agent as agt
    from .. import channel as chan
    from .. import message as msg


async def render_message_template(
        path: str | pathlib.Path, **format_kwargs: str) -> str:
    """
    Render a message template.

    Finds the template file belonging to the given path, which must be relative
    to the directory containing message templates and not include the .template
    suffix. Reads the template file and calls format() using the given
    format_kwargs.

    Raises FileNotFoundError if no template with the given path exists.
    """
    if not isinstance(path, pathlib.Path):
        path = pathlib.Path(path)
    template_suffix = path.suffix + ".template"
    template_path = path.with_suffix(template_suffix)
    template = await base.read_file("message_templates", template_path)
    return template.format(**format_kwargs)


async def list_tutorial_topics() -> list[str]:
    """List all available tutorial topics."""
    def list_topics(templates_dir: pathlib.Path):
        tutorials_dir = templates_dir / "tutorial"
        return sorted(
            file.name.removesuffix(".md.template")
            for file in tutorials_dir.glob("*.md.template"))

    return await asyncio.to_thread(
        base.do_with_resource_dir, "message_templates", list_topics)


async def render_tutorial(topic: str) -> str:
    """
    Render a tutorial template by topic.

    Raises FileNotFoundError if tutorial exists for the given topic.
    """
    return await render_message_template(f"tutorial/{topic}.md")


async def render_channel_status(
        channel: "chan.Channel", available: bool) -> str:
    """
    Render a channel status message.

    Depending on the available flag, renders either a message saying the
    channel can be used (including channel details), or one saying it's
    unavailable.
    """
    if available:
        channel_status = await channel.status
        return await render_message_template(
            "channel_status_available.md", channel_type=channel.type,
            status_json=channel_status.model_dump_json())
    return await render_message_template(
        "channel_status_unavailable.md", channel_type=channel.type)


async def render_file_content(
        workspace_directory: pathlib.Path,
        relative_file_path: pathlib.Path) -> str:
    """
    Render a file content message.

    Reads the file at relative_file_path relative to workspace_directory. If
    the file is empty, indicates <file is empty> in the rendered message,
    similarly <file does not exist>.
    """
    file_path = workspace_directory / relative_file_path
    try:
        content = await asyncio.to_thread(file_path.read_text)
    except FileNotFoundError:
        content = "<file does not exist>"
    if not content:
        content = "<file is empty>"
    return await render_message_template(
        "file_content.md", file_path=relative_file_path, content=content)


async def render_workspace_info(agent: "agt.Agent") -> str:
    """
    Render a message informing the agent about their workspace.

    This message includes a list and description of all their personality
    files.
    """
    personality_files_description = "\n".join([
        f"- {pf.path}: {pf.description}"
        for pf in agent.information.personality.personality_files])
    return await render_message_template(
        "system_information/workspace.md", workspace_dir=agent.workspace_dir,
        personality_files=personality_files_description)


async def render_message_send_error(
        message: "msg.AgentMessage", exc: Exception) -> str:
    """Render a message informing the agent about a message send error."""
    return await render_message_template(
        "system_information/send_error.md",
        channel_descriptor=message.metadata.channel.model_dump_json(),
        traceback="".join(traceback.format_exception(exc, limit=10)))
