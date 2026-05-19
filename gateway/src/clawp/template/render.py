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
import importlib.resources
import pathlib

from .. import model as mdl


class TemplateNotFoundError(FileNotFoundError):
    pass


def _do_with_templates_dir(function):
    templates_resource = importlib.resources.files("clawp.template.messages")
    with importlib.resources.as_file(templates_resource) as templates_dir:
        return function(templates_dir)


async def render_message_template(
        path: str | pathlib.Path, **format_kwargs: str) -> str:
    """
    Render a message template.

    Finds the template file belonging to the given path, which must be relative
    to the directory containing message templates and not include the .template
    suffix. Reads the template file and calls format() using the given
    format_kwargs.

    Raises TemplateNotFoundError if no template with the given path exists.
    """
    if not isinstance(path, pathlib.Path):
        path = pathlib.Path(path)

    def read_file(templates_dir: pathlib.Path):
        file_path = templates_dir / path
        template_suffix = file_path.suffix + ".template"
        template_path = file_path.with_suffix(template_suffix)
        try:
            with template_path.open() as f:
                return f.read()
        except FileNotFoundError as e:
            raise TemplateNotFoundError(
                f"template {path} doesn't exist") from e

    template = await asyncio.to_thread(_do_with_templates_dir, read_file)
    return template.format(**format_kwargs)


async def render_tutorial(topic: str) -> str:
    """
    Render a tutorial template by topic.

    Raises TemplateNotFoundError if tutorial exists for the given topic.
    """
    return await render_message_template(f"tutorial/{topic}.md")


async def render_channel_status(channel_status: mdl.ChannelStatus) -> str:
    """Render a channel status message."""
    if channel_status.available:
        template = "channel_status_available.md"
    else:
        template = "channel_status_unavailable.md"
    return await render_message_template(
        template, channel_type=channel_status.type,
        status_json=channel_status.model_dump_json())
