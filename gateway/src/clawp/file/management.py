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
        raise NotImplementedError


@dc.dataclass
class InfoMessage:
    spec: mdl.InfoMessageSpec[t.Any]
    message_type: type[msg.DeveloperMessage | msg.SystemMessage]
    content: str


class InfoManager:
    def __init__(self, agent: agt.Agent) -> None:
        self._agent = agent

    async def missing_messages(
        self, session_state: mdl.SessionState
    ) -> list[InfoMessage]:
        required_specs = self._agent.info_message_specs
        missing_specs = required_specs - session_state.info_messages_shown
        messages = []
        for spec in missing_specs:
            if isinstance(spec, mdl.InfoMessageSpecInit):
                message_type = msg.DeveloperMessage
                content = await template.render_message_template(
                    "init_system.txt"
                )
            elif isinstance(spec, mdl.InfoMessageSpecTutorial):
                message_type = msg.DeveloperMessage
                content = await template.render_tutorial(spec.topic)
            else:
                assert isinstance(spec, mdl.InfoMessageSpecFileContent)
                message_type = msg.SystemMessage
                content = await template.render_file_content(
                    self._agent.workspace_dir, spec.file_path
                )
            messages.append(
                InfoMessage(
                    spec=spec, message_type=message_type, content=content
                )
            )
        return messages
