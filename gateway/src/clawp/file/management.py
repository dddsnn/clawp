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

import abc
import dataclasses as dc
import logging
import typing as t

from .. import message as msg
from .. import model as mdl
from . import template

if t.TYPE_CHECKING:
    from .. import agent as agt


class InfoProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def info_message_specs(self) -> frozenset[mdl.InfoMessageSpec[t.Any]]:
        """
        TODO++++++++++++++
        """
        raise NotImplementedError


@dc.dataclass
class InfoMessage:
    spec: mdl.InfoMessageSpec[t.Any]
    message_type: type[msg.DeveloperMessage | msg.SystemMessage]
    content: str


# REFACTOR name?+++++++++++++
class InfoManager:
    """
    TODO++++++++++++++
    """

    _TUTORIAL_ORDER = (
        "tutorials",
        "system_sessions",
        "system_system_messages",
        "system_channels_chats",
        "channel_web_ui",
        "channel_agent",
        "channel_github",
        "channel_matrix",
        "channel_system",
        "system_workspace_memory",
    )

    def __init__(self, agent: agt.Agent) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._agent = agent

    # REFACTOR+++++++++++++++
    async def missing_messages(
        self, session_state: mdl.SessionState
    ) -> list[InfoMessage]:
        """
        TODO++++++++++++++
        """
        required_specs = self._agent.info_message_specs
        missing_specs = required_specs - session_state.info_messages_shown
        missing_specs = sorted(missing_specs, key=self._message_order)
        return [await self._make_message_from_spec(s) for s in missing_specs]

    # TODO sort specs+++++++++++
    def _message_order(self, spec: mdl.InfoMessageSpec[t.Any]):
        if isinstance(spec, mdl.InfoMessageSpecInit):
            # Init message at the very top.
            return (0, 0)
        elif isinstance(spec, mdl.InfoMessageSpecTutorial):
            # Tutorials next, according to their order.
            try:
                index = self._TUTORIAL_ORDER.index(spec.topic)
            except ValueError:
                self._logger.warning(
                    f"Unknown tutorial topic {spec.topic}, appending to the "
                    "end."
                )
                index = float("inf")
            return (1, index)
        return (2, 0)

    async def _make_message_from_spec(
        self, spec: mdl.InfoMessageSpec[t.Any]
    ) -> InfoMessage:
        if isinstance(spec, mdl.InfoMessageSpecInit):
            message_type = msg.DeveloperMessage
            content = await template.render_message_template("init_system.txt")
        elif isinstance(spec, mdl.InfoMessageSpecTutorial):
            message_type = msg.DeveloperMessage
            content = await template.render_tutorial(spec.topic)
        else:
            assert isinstance(spec, mdl.InfoMessageSpecFileContent)
            message_type = msg.SystemMessage
            content = await template.render_file_content(
                self._agent.workspace_dir, spec.file_path
            )
        return InfoMessage(
            spec=spec, message_type=message_type, content=content
        )

    # TODO warn if unsure where to put msg in ordering+++++++++++
    # TODO for ordering of files: hint what kind of file it is (personality vs. config vs. anything else)
