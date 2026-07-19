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
import contextlib
import logging
import typing as t

import github
import github.Issue as gh_iss
import pydantic as pyd
import whenever as we

from .. import message as msg
from .. import model as mdl
from . import base


class GithubAppClient:
    """Small wrapper around PyGithub Github App authentication."""
    def __init__(self, config: mdl.GithubAccountConfig) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._config = config
        app_auth = github.Auth.AppAuth(
            app_id=self._config.app_id,
            private_key=self._config.private_key.value)
        self._integration = github.GithubIntegration(auth=app_auth)
        self._login = None

    async def __aenter__(self) -> t.Self:
        self._login = await asyncio.to_thread(self._get_login_sync)
        return self

    async def __aexit__(self, *_) -> bool:
        await asyncio.to_thread(self._integration.close)
        self._login = None
        return False

    def _get_login_sync(self) -> str:
        app_slug = self._integration.get_app_installation(
            self._config.installation_id).app_slug
        return app_slug + "[bot]"

    @property
    def running(self) -> bool:
        """Whether the client is running."""
        return self._login is not None

    @property
    def login(self) -> str:
        """
        Login of the installed app.

        This is the app slug followed by [bot].
        """
        assert self._login is not None
        return self._login

    def get_installation_github(self) -> github.Github:
        return self._integration.get_github_for_installation(
            self._config.installation_id)

    def get_installation_token(self) -> str:
        return self._integration.get_access_token(
            self._config.installation_id).token

    def list_installation_repositories(self) -> list[str]:
        installation = self._integration.get_app_installation(
            self._config.installation_id)
        repos = []
        for repo in installation.get_repos():
            if not repo.organization.login == self._config.organization:
                self._logger.debug(
                    f"Ignoring installation repo {repo} which is not owned by "
                    f"{self._config.organization}.")
                continue
            repos.append(repo.full_name)
        return repos


class GithubChannel(base.Channel):
    def __init__(
            self, config: mdl.GithubAccountConfig,
            state: mdl.GithubChannelState) -> None:
        super().__init__("github")
        self._config = config
        self._state = state
        self._client = GithubAppClient(self._config)
        self._messages: dict[str, list[tuple[int, mdl.GithubChatMessage]]] = {}
        self._poll_task: asyncio.Task | None = None

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        await self._client.__aenter__()
        self._poll_task = asyncio.create_task(self._poll_forever())
        return self

    async def __aexit__(self, *args) -> bool:
        assert self._poll_task is not None
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        except Exception:
            self._logger.exception("Github poll task failed while stopping.")
        await self._client.__aexit__(*args)
        return await super().__aexit__(*args)

    @property
    def id(self) -> str:
        return self._config.id

    @property
    async def status(self) -> mdl.GithubChannelStatus:
        cm = contextlib.nullcontext()
        if not self._client.running:
            # If the client isn't running, start it to get the login.
            cm = self._client
        async with cm:
            return mdl.GithubChannelStatus(
                available=True, app_id=self._config.app_id,
                installation_id=self._config.installation_id,
                login=self._client.login)

    async def get_chat_descriptor(
            self, chat_id: str) -> mdl.GithubChatDescriptor:
        try:
            return mdl.GithubChatDescriptor.from_chat_id(chat_id)
        except (ValueError, pyd.ValidationError) as e:
            raise base.ChatIdError("invalid chat ID") from e

    async def get_unread_messages(self,
                                  chat_id: str) -> list[mdl.GithubChatMessage]:
        chat = mdl.GithubChatDescriptor.from_chat_id(chat_id)
        try:
            messages = self._messages[chat.chat_id]
        except KeyError:
            # No messages at all for this chat_id.
            return []

        self._state.last_read_event_ids[chat.repo_full_name] = messages[-1][0]
        # Mark as read by deleting the local copy.
        del self._messages[chat.chat_id]
        return [m for _, m in messages]

    def make_outgoing_start_metadata(
        self, chat: mdl.GithubChatDescriptor
    ) -> tuple[mdl.GithubStartMessageMetadata,
               type[mdl.GithubChatMessageMetadata]]:
        assert isinstance(chat, mdl.GithubChatDescriptor)
        # comment_type is comment, since sending a message only works when the
        # issue already exists (so it can't be the description).
        return (
            mdl.GithubStartMessageMetadata(chat=chat, comment_type="comment"),
            mdl.GithubChatMessageMetadata)

    async def send(self, message: msg.AgentMessage) -> None:
        chat = message.metadata.chat
        assert isinstance(chat, mdl.GithubChatDescriptor)
        body = await message.content

        def _create_comment():
            gh = self._client.get_installation_github()
            repo = gh.get_repo(chat.repo_full_name)
            issue = repo.get_issue(chat.issue_number)
            return issue.create_comment(body)

        await asyncio.to_thread(_create_comment)

    async def _poll_forever(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Error while polling Github events.")
            await asyncio.sleep(self._config.poll_interval.total("seconds"))

    async def _poll_once(self) -> None:
        repositories = await asyncio.to_thread(
            self._client.list_installation_repositories)
        for repo_full_name in repositories:
            await self._poll_repo(repo_full_name)

    async def _poll_repo(self, repo_full_name: str) -> None:
        new_events = await asyncio.to_thread(
            self._collect_new_repo_issue_events, repo_full_name)
        if not new_events:
            return
        for event in reversed(new_events):
            await self._process_event(repo_full_name, event)
            self._state.last_read_event_ids[repo_full_name] = event.id

    def _collect_new_repo_issue_events(
            self, repo_full_name: str) -> list[gh_iss.IssueEvent]:
        read_event_id = self._state.last_read_event_ids.get(repo_full_name)
        gh = self._client.get_installation_github()
        repo = gh.get_repo(repo_full_name)
        new_issue_events: list[gh_iss.IssueEvent] = []
        for event in repo.get_issues_events():
            if event.id == read_event_id:
                break
            new_issue_events.append(event)
            return new_issue_events

    async def _process_event(
            self, repo_full_name: str, issue_event: gh_iss.IssueEvent) -> None:
        actor_login = getattr(issue_event.actor, "login", "")
        if actor_login == self._client.login:
            # Avoid loops by ignoring our own events.
            return
        messages = []
        if issue_event.event == "labeled":
            if issue_event.label.name != f"agent-assigned:{ self._client.login}":
                return
            messages += self._messages_from_assignment(
                repo_full_name, issue_event)
        for message in messages:
            self._messages.setdefault(message.metadata.chat.chat_id,
                                      []).append((issue_event.id, message))
            await self._publisher.append(message)

    def _messages_from_assignment(
            self, repo_full_name: str,
            issue_event: gh_iss.IssueEvent) -> list[mdl.GithubChatMessage]:
        label_message_content = (
            f"You were assigned issue #{issue_event.issue.number} by "
            f"{issue_event.actor.login} (they added the "
            f"{issue_event.label.name} label).")
        if issue_event.issue.comments > 0:
            label_message_content += (
                f"\n\n Showing {issue_event.issue.comments} existing messages "
                "in the issue.")
        messages = []
        messages.append(
            self._make_message(
                "issue", repo_full_name, issue_event.issue.number, "comment",
                issue_event.created_at, label_message_content))
        description_content = issue_event.issue.body
        if not description_content:
            description_content = "No description provided."
        messages.append(
            self._make_message(
                "issue", repo_full_name, issue_event.issue.number,
                "description", issue_event.created_at, description_content))
        if issue_event.issue.comments:
            for comment in issue_event.issue.get_comments():
                messages.append(
                    self._make_message(
                        "issue", repo_full_name, issue_event.issue.number,
                        "comment", comment.created_at, comment.body))
        return messages

    def _make_message(
            self, issue_type, repo_full_name, issue_number, comment_type,
            created_at, content) -> mdl.GithubChatMessage:
        chat = mdl.GithubChatDescriptor(
            channel="github", chat_id=mdl.GithubChatDescriptor.create_chat_id(
                issue_type, repo_full_name,
                issue_number), issue_type=issue_type,
            repo_full_name=repo_full_name, issue_number=issue_number)
        return mdl.GithubChatMessage(
            role="user", metadata=mdl.GithubChatMessageMetadata(
                time=we.Instant(created_at), chat=chat,
                comment_type=comment_type), content=content)
