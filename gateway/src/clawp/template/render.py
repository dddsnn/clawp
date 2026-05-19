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


def _read_file(path: pathlib.Path) -> str:
    with path.open() as f:
        return f.read()


async def render_message_template(
        *path_components: str, **format_kwargs: str) -> str:
    """
    Render a message template.

    Takes the given path components, appends them to the directory containing
    message templates, and finally appends a .template suffix to determine the
    file path. Reads the file and calls format() using the given format_kwargs.
    """
    templates_resource = importlib.resources.files("clawp.template.messages")
    with importlib.resources.as_file(templates_resource) as templates_dir:
        file_path = templates_dir / pathlib.Path(*path_components)
        template_suffix = file_path.suffix + ".template"
        template_path = file_path.with_suffix(template_suffix)
        template = await asyncio.to_thread(_read_file, template_path)
        return template.format(**format_kwargs)
