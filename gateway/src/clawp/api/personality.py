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

import logging

import fastapi
import fastapi.exceptions as fa_exc

from .. import file
from .. import model as mdl

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/personalities")


@router.get("")
async def list_personalities() -> list[mdl.AgentPersonality]:
    """Get a list of available personalities."""
    personality_names = await file.list_personalities()
    return [await file.read_personality(name) for name in personality_names]


@router.get("/{personality_name}")
async def get_personality(
    personality_name: str,
) -> mdl.AgentPersonalityWithFileContents:
    """
    Get a personality with initial file contents.

    A personality is initialized with a set of personality files in the agent's
    workspace.
    """
    try:
        return await file.read_personality_with_file_contents(personality_name)
    except file.PersonalityNotFoundError:
        raise fa_exc.HTTPException(
            status_code=404, detail=f"No personality named {personality_name}."
        )
