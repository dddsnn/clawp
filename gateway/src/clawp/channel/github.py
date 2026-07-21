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
import dataclasses as dc
import datetime as dt
import logging
import operator as op
import typing as t

import github
import github.Issue as gh_iss
import github.Repository as gh_repo
import pydantic as pyd
import whenever as we

from .. import message as msg
from .. import model as mdl
from . import base


class GithubAppClient:
    """Github client authenticating as a Github app."""
    def __init__(self, config: mdl.GithubAccountConfig) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._config = config
        app_auth = github.Auth.AppAuth(
            app_id=self._config.app_id,
            private_key=self._config.private_key.value)
        # Use lazy=False to not accidentally block the event loop when we
        # access a property and cause a request to be sent.
        self._integration = github.GithubIntegration(auth=app_auth, lazy=False)
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

    def get_github(self) -> github.Github:
        """Get a Github instance for the app's installation."""
        return self._integration.get_github_for_installation(
            self._config.installation_id)

    async def get_installation_token(self) -> str:
        """
        Get an authorization token for the app.

        This token can be used with the gh CLI (GH_TOKEN).
        """
        authorization = await asyncio.to_thread(
            self._integration.get_access_token, self._config.installation_id)
        return authorization.token

    async def list_installation_repositories(self) -> list[gh_repo.Repository]:
        """
        List the installation's repositories.

        Lists all repositories belonging to the configured organization that
        the app's installation has access to.
        """
        return await asyncio.to_thread(
            self._list_installation_repositories_sync)

    def _list_installation_repositories_sync(self) -> list[gh_repo.Repository]:
        installation = self._integration.get_app_installation(
            self._config.installation_id)
        repos: list[gh_repo.Repository] = []
        for repo in installation.get_repos():
            if not repo.organization.login == self._config.organization:
                self._logger.debug(
                    f"Ignoring installation repo {repo} which is not owned by "
                    f"{self._config.organization}.")
                continue
            repos.append(repo)
        return repos


@dc.dataclass
class IncomingGithubMessage(base.IncomingMessage):
    chat: mdl.GithubChatDescriptor
    message: mdl.GithubChatMessage | mdl.SystemMessage
    event_id: int


class GithubChannel(base.Channel):
    """
    Github channel.

    This channel presents Github issues and pull requests as chats, in which
    comments are messages. Agents are presented with issue/PR comments if they
    are assigned to them with a label named agent-assigned:<agent_login>, where
    <agent_login> is the agent's login name (ending in [bot]).

    Uses polling of the Github API.
    """
    def __init__(
            self, config: mdl.GithubAccountConfig,
            state: mdl.GithubChannelState) -> None:
        super().__init__("github")
        self._config = config
        self._state = state
        self._client = GithubAppClient(self._config)
        self._incoming_messages: dict[str, list[IncomingGithubMessage]] = {}
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

    @property
    def agent_assigned_label(self) -> str:
        """The issue label meaning this agent is assigned."""
        return f"agent-assigned:{self._client.login}"

    async def get_chat_descriptor(
            self, chat_id: str) -> mdl.GithubChatDescriptor:
        try:
            return mdl.GithubChatDescriptor.from_chat_id(chat_id)
        except (ValueError, pyd.ValidationError) as e:
            raise base.ChatIdError("invalid chat ID") from e

    async def num_unread_messages(self, chat_id: str) -> int:
        chat = await self.get_chat_descriptor(chat_id)
        try:
            return len(self._incoming_messages[chat.chat_id])
        except KeyError:
            return 0

    async def get_unread_messages(self,
                                  chat_id: str) -> list[IncomingGithubMessage]:
        chat = await self.get_chat_descriptor(chat_id)
        try:
            incoming_messages = self._incoming_messages[chat.chat_id]
        except KeyError:
            # No messages at all for this chat_id.
            return []
        self._state.last_read_event_ids[chat.repo_full_name] = (
            incoming_messages[-1].event_id)
        # Mark as read by deleting the local copy.
        del self._incoming_messages[chat.chat_id]
        return incoming_messages

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
        await asyncio.to_thread(
            self._create_comment, chat, await message.content)

    def _create_comment(
            self, chat: mdl.GithubChatDescriptor, comment_body: str):
        gh = self._client.get_github()
        repo = gh.get_repo(chat.repo_full_name)
        issue = repo.get_issue(chat.issue_number)
        return issue.create_comment(comment_body)

    async def _poll_forever(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Error while polling Github.")
            await asyncio.sleep(self._config.poll_interval.total("seconds"))

    async def _poll_once(self) -> None:
        repositories = await self._client.list_installation_repositories()
        for repo in repositories:
            try:
                await self._poll_repo(repo)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    f"Error while polling repository {repo.full_name}.")

    async def _poll_repo(self, repo: gh_repo.Repository) -> None:
        new_events = await asyncio.to_thread(self._get_new_issue_events, repo)
        if not new_events:
            return
        await self._process_events(repo, new_events)

    def _get_new_issue_events(
            self, repo: gh_repo.Repository) -> list[gh_iss.IssueEvent]:
        read_event_id = self._state.last_read_event_ids.get(repo.full_name)
        new_issue_events: list[gh_iss.IssueEvent] = []
        for issue_event in repo.get_issues_events():
            # The issue events are sorted newest-first, so collect them until
            # we come across the first event we've already seen.
            if issue_event.id == read_event_id:
                break
            new_issue_events.append(issue_event)
        # Sort by creation timestamp so we're returning oldest events first.
        new_issue_events.sort(key=op.attrgetter("created_at"))
        return new_issue_events

    async def _process_events(
            self, repo: gh_repo.Repository,
            issue_events: list[gh_iss.IssueEvent]) -> None:
        issue_stati = await asyncio.to_thread(
            self._issue_state_changes, issue_events)
        self._logger.info(f"got issue stati {issue_stati}")
        for issue_event in issue_events:
            messages: list[IncomingGithubMessage] = []
            try:
                assigned, event_id = issue_stati[issue_event.issue.number]
                if issue_event.id == event_id:
                    # This is the event of assignment state change.
                    messages += await asyncio.to_thread(
                        self._incoming_messages_for_assignment_change, repo,
                        issue_event, assigned)
                elif assigned:
                    messages += await asyncio.to_thread(
                        self._incoming_messages_for_event, repo, issue_event)
            finally:
                self._state.last_read_event_ids[repo.full_name] = (
                    issue_event.id)
            for message in messages:
                self._incoming_messages.setdefault(message.chat.chat_id,
                                                   []).append(message)
                await self._publisher.append(message)

    def _issue_state_changes(
        self, issue_events: list[gh_iss.IssueEvent]
    ) -> dict[int, tuple[bool, bool, int
                         | None]]:
        """
        Determine assignment/unassignment state and change.

        Goes through the list of issue events and determines based on the label
        whether the issue is currently assigned, and if the state changed in
        the events. If an issues state changes multiple times such that its end
        state ends up as the start state, it is listed as not changed.

        Returns a dict mapping each issue number to a tuple (assigned,
        event_id), where event_id is the ID of the last event in which the
        state changed. If it is None, this means the state hasn't changed.
        """
        stati = {}
        for issue_event in issue_events:
            try:
                assigned, event_id = stati[issue_event.issue.number]
            except KeyError:
                assigned = any(
                    label.name == self.agent_assigned_label
                    for label in issue_event.issue.labels)
                event_id = None
            label_added = (
                issue_event.event == "labeled"
                and issue_event.label.name == self.agent_assigned_label)
            label_removed = (
                issue_event.event == "unlabeled"
                and issue_event.label.name == self.agent_assigned_label)
            assert not (label_added and label_removed)
            if (assigned and label_added) or (not assigned and label_removed):
                event_id = issue_event.id
            elif assigned and label_removed:
                self._logger.debug(
                    f"Issue {issue_event.issue.number} is currently assigned, "
                    "but found label removed. Must have been added/removed "
                    "multiple times.")
                event_id = None
            elif not assigned and label_added:
                self._logger.debug(
                    f"Issue {issue_event.issue.number} is currently not "
                    "assigned, but found label added. Must have been "
                    "added/removed multiple times.")
                event_id = None
            stati[issue_event.issue.number] = (assigned, event_id)
        return stati

    def _incoming_messages_for_assignment_change(
            self, repo: gh_repo.Repository, issue_event: gh_iss.IssueEvent,
            assigned: bool) -> list[IncomingGithubMessage]:
        if not assigned:
            content = (
                f"You were unassigned from issue #{issue_event.issue.number} "
                f"by {issue_event.actor.login} (they removed the "
                f"{issue_event.label.name} label). You will receive no "
                "further messages from this issue. There is no need to "
                "acknowledge this.")
            return [self._make_system_message(issue_event, repo, content)]
        label_message_content = (
            f"You were assigned issue #{issue_event.issue.number} by "
            f"{issue_event.actor.login} (they added the "
            f"{issue_event.label.name} label).")
        if issue_event.issue.comments > 0:
            label_message_content += (
                f"\n\n Showing {issue_event.issue.comments} existing "
                "message(s) in the issue.")
        messages: list[IncomingGithubMessage] = []
        messages.append(
            self._make_system_message(
                issue_event, repo, label_message_content))
        description_content = issue_event.issue.body
        if not description_content:
            description_content = "No description provided."
        messages.append(
            self._make_user_message(
                issue_event, repo, "description", issue_event.created_at,
                description_content))
        if issue_event.issue.comments:
            for comment in issue_event.issue.get_comments():
                messages.append(
                    self._make_user_message(
                        issue_event, repo, "comment", comment.created_at,
                        comment.body))
        return messages

    def _make_system_message(
            self, issue_event: gh_iss.IssueEvent, repo: gh_repo.Repository,
            content: str) -> IncomingGithubMessage:
        chat = self._make_chat_descriptor(issue_event, repo)
        system_message = mdl.SystemMessage(
            metadata=mdl.InternalMessageMetadata(
                time=we.Instant(issue_event.created_at)), content=content)
        return IncomingGithubMessage(
            chat=chat, message=system_message, event_id=issue_event.id)

    def _make_chat_descriptor(
            self, issue_event: gh_iss.IssueEvent,
            repo: gh_repo.Repository) -> mdl.GithubChatDescriptor:
        return mdl.GithubChatDescriptor(
            channel="github", chat_id=mdl.GithubChatDescriptor.create_chat_id(
                "issue", repo.full_name, issue_event.issue.number),
            issue_type="issue", repo_full_name=repo.full_name,
            issue_number=issue_event.issue.number)

    def _make_user_message(
            self, issue_event: gh_iss.IssueEvent, repo: gh_repo.Repository,
            comment_type: t.Literal["description", "comment"],
            created_at: dt.datetime, content: str) -> IncomingGithubMessage:
        chat = self._make_chat_descriptor(issue_event, repo)
        user_message = mdl.GithubChatMessage(
            role="user", metadata=mdl.GithubChatMessageMetadata(
                time=we.Instant(created_at), chat=chat,
                comment_type=comment_type), content=content)
        return IncomingGithubMessage(
            chat=chat, message=user_message, event_id=issue_event.id)

    def _incoming_messages_for_event(
            self, repo: gh_repo.Repository,
            issue_event: gh_iss.IssueEvent) -> list[IncomingGithubMessage]:
        assert issue_event.event not in ["labeled", "unlabeled"]
        return []
