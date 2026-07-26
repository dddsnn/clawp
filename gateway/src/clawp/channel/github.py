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
import datetime as dt
import logging
import operator as op
import typing as t

import github
import github.Issue as gh_iss
import github.Repository as gh_repo
import httpx
import pydantic as pyd
import whenever as we

from .. import file
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
        self._github = None
        self._authorization = None

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

    @property
    def github(self) -> github.Github:
        """Github instance for the app's installation."""
        if not self._github:
            self._github = self._integration.get_github_for_installation(
                self._config.installation_id)
        return self._github

    @property
    async def installation_token(self) -> str:
        """
        Authorization token for the app installation.

        The token is guaranteed to be valid for at least another minute.

        This token can be used with the gh CLI (GH_TOKEN).
        """
        if (self._authorization is None
                or we.Instant(self._authorization.expires_at)
                < we.Instant.now() + we.TimeDelta(minutes=1)):
            self._logger.info("Fetching new installation token.")
            self._authorization = await asyncio.to_thread(
                self._integration.get_access_token,
                self._config.installation_id)
        return self._authorization.token

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


class RepositoryProgressChecker:
    """
    Utility checking whether there is any progress in the API.

    This is a context manager that makes requests to selected endpoints making
    use of ETag/If-None-Match headers. The new_issues_events_available is
    available when the context manager is active, indicating whether a request
    to the issues events endpoint would yield new results.

    The ETag value of any response is persisted when the context manager exits
    and provided as If-None-Match header on the next request. Checks the
    response's status code, where 200 means there are new events, 304 means no
    new events.

    If an exception occurs when querying the API, defaults to indicating that
    there are new results (so that nothing is skipped accidentally). The same
    applies when an exception occurs within the context manager (i.e. the code
    wrapped by it). This applies that client code must be ready to receive
    events it has seen before and filter them appropriately.

    This only works as intended if the client code processes all events within
    the context manager. Once the context manager exits, it is asssumed they
    never need to be received again. If an error occurs in the processing code,
    an exception must be raised in the context manager so that the events are
    not marked as seen and can be tried again.

    The context manager is reentrant.
    """
    def __init__(
            self, github_client: GithubAppClient,
            read_progress: mdl.GithubRepositoryReadProgress,
            repo_full_name: str, httpx_client: httpx.AsyncClient) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._github_client = github_client
        self._read_progress = read_progress
        self._issues_events_url = (
            f"https://api.github.com/repos/{repo_full_name}/issues/events"
            "?per_page=1")
        self._httpx_client = httpx_client
        self._active_etag = None
        self._new_issues_events_available = None

    async def __aenter__(self) -> t.Self:
        self._new_issues_events_available = True
        self._active_etag = None
        await self._update()
        return self

    async def __aexit__(
            self, exc_type: type[BaseException] | None, *_) -> t.Self:
        self._new_issues_events_available = None
        if self._active_etag is None:
            self._logger.warning(
                f"{self._issues_events_url} responded without an ETag.")
        elif exc_type is None:
            self._read_progress.issues_event_etag = self._active_etag
        else:
            self._logger.debug(
                f"Discarding ETag because of exception in context manager "
                f"processing events from {self._issues_events_url}.")
        return False

    async def _update(self) -> None:
        headers = {
            "Authorization": "Bearer " +
            (await self._github_client.installation_token),
            "Accept": "application/vnd.github+json"}
        if self._read_progress.issues_event_etag is not None:
            headers["If-None-Match"] = self._read_progress.issues_event_etag
        try:
            response = await self._httpx_client.get(
                self._issues_events_url, headers=headers)
            self._active_etag = response.headers.get("ETag")
            self._new_issues_events_available = response.status_code != 304
            if response.status_code not in (200, 304):
                self._logger.warning(
                    f"Unexpected status {response.status_code} in response "
                    f"from {self._issues_events_url}.")
        except Exception:
            self._logger.exception(
                "Error checking ETag, setting events as available.")
            self._new_issues_events_available = True

    @property
    def new_issues_events_available(self) -> bool:
        """
        Whether any new issues events are available.

        Indicates whether a query to the issues events endpoint will return
        events that didn't exist since the last time the context manager
        exited.

        This applies to the
        https://api.github.com/repos/{owner}/{repo}/issues/events endpoint.

        The context manager must be active or this raises an exception.
        """
        if self._new_issues_events_available is None:
            raise ValueError("context manager is not active")
        return self._new_issues_events_available


class ProgressChecker:
    """
    Utility checking whether there is any progress in the API.

    This is just a manager for the repo-specific checkers that manages their
    state and a shared http client.

    The checker should be closed with aclose() on shutdown.
    """
    def __init__(
            self, github_client: GithubAppClient,
            state: mdl.GithubChannelState) -> None:
        self._github_client = github_client
        self._state = state
        self._httpx_client = httpx.AsyncClient()

    async def aclose(self) -> None:
        await self._httpx_client.aclose()

    def for_repo(self, repo_full_name: str) -> RepositoryProgressChecker:
        """
        Get a repo-specific checker.

        Creates a new read progress state for the repo if necessary.
        """
        read_progress = self._state.repo_read_progress.setdefault(
            repo_full_name, mdl.GithubRepositoryReadProgress())
        return RepositoryProgressChecker(
            self._github_client, read_progress, repo_full_name,
            self._httpx_client)


class GithubChannel(base.Channel):
    """
    Github channel.

    This channel presents Github issues and pull requests as chats, in which
    comments are messages. Agents are presented with issue/PR comments if they
    are assigned to them with a label named agent-assigned:<agent_login>, where
    <agent_login> is the agent's login name (ending in [bot]).

    Uses polling of the Github API.

    The channel also makes environment variables available that authorize git
    and the gh CLI.
    """
    _RELEVANT_ISSUE_EVENTS = ["labeled", "unlabeled"]

    def __init__(
            self, config: mdl.GithubAccountConfig,
            state: mdl.GithubChannelState) -> None:
        super().__init__("github")
        self._config = config
        self._state = state
        self._client = GithubAppClient(self._config)
        self._progress_checker = ProgressChecker(self._client, self._state)
        self._poll_task: asyncio.Task | None = None
        self._message_templates: dict[str, file.Template] = None

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        self._message_templates = {
            "assigned": await file.read_message_template(
                "system_information/github_assigned.md"),
            "unassigned": await file.read_message_template(
                "system_information/github_unassigned.md"),}
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
        await self._progress_checker.aclose()
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

    async def get_extra_shell_env(self) -> dict[str, str]:
        # We're using the GIT_CONFIG_* env variables, which allow us to specify
        # extra config without having to write to a file.
        token = await self._client.installation_token
        url_rewrite_config_key = (
            'url."https://{}:{}@github.com/".insteadOf'.format(
                self._client.login, token))
        return {
            "GH_TOKEN": token,
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": url_rewrite_config_key,
            "GIT_CONFIG_VALUE_0": "https://github.com/",
            "GIT_CONFIG_KEY_1": "user.name",
            "GIT_CONFIG_VALUE_1": self._client.login,
            "GIT_CONFIG_KEY_2": "user.email",
            "GIT_CONFIG_VALUE_2": self._config.agent_email,}

    async def get_chat_descriptor(
            self, chat_id: str) -> mdl.GithubChatDescriptor:
        try:
            issue_type, repo_full_name, issue_number = (
                mdl.GithubChatDescriptor.parse_chat_id(chat_id))
        except (ValueError, pyd.ValidationError) as e:
            raise base.ChatIdError("invalid chat ID") from e
        issue = await asyncio.to_thread(
            self._get_issue_sync, repo_full_name, issue_number)
        return mdl.GithubChatDescriptor(
            chat_id=chat_id,
            issue_type=issue_type,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            issue_title=issue.title,
            issue_author=issue.user.login,
        )

    def _get_issue_sync(
            self, repo_full_name: str, issue_number: int) -> gh_iss.Issue:
        repo = self._client.github.get_repo(repo_full_name)
        issue = repo.get_issue(issue_number)
        return issue.complete()

    async def num_unread_messages(self, chat_id: str) -> int:
        chat = await self.get_chat_descriptor(chat_id)
        try:
            return len(self._state.unread_messages[chat.chat_id])
        except KeyError:
            return 0

    async def get_unread_messages(
            self, chat_id: str) -> list[mdl.IncomingGithubMessage]:
        chat = await self.get_chat_descriptor(chat_id)
        try:
            incoming_messages = self._state.unread_messages[chat.chat_id]
        except KeyError:
            # No messages at all for this chat_id.
            return []
        # Mark the messages as read by deleting them from the state.
        del self._state.unread_messages[chat.chat_id]
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
        repo = self._client.github.get_repo(chat.repo_full_name)
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
        async with self._progress_checker.for_repo(
                repo.full_name) as repo_checker:
            if repo_checker.new_issues_events_available:
                issue_events = await asyncio.to_thread(
                    self._get_relevant_new_issue_events, repo)
                self._logger.debug(
                    f"Got {len(issue_events)} new events polling repository "
                    f"{repo.full_name}.")
            else:
                issue_events = []
            if issue_events:
                await self._process_events(repo, issue_events)

    def _get_relevant_new_issue_events(
            self, repo: gh_repo.Repository) -> list[gh_iss.IssueEvent]:
        read_marker = self._get_read_marker(repo.full_name)
        new_issue_events: list[gh_iss.IssueEvent] = []
        for issue_event in repo.get_issues_events():
            # The issue events are sorted newest-first, so collect them until
            # we come across the first event we've already seen.
            this_event_time = we.Instant(issue_event.created_at)
            already_seen = (
                this_event_time < read_marker.last_event_time
                or issue_event.id in read_marker.last_event_ids)
            if already_seen:
                break
            if issue_event.event in self._RELEVANT_ISSUE_EVENTS:
                new_issue_events.append(issue_event)
        # Sort by creation timestamp so we're returning oldest events first.
        new_issue_events.sort(key=op.attrgetter("created_at"))
        return new_issue_events

    def _get_read_marker(
            self, repo_full_name: str) -> mdl.GithubEventReadMarker:
        read_progress = self._state.repo_read_progress.setdefault(
            repo_full_name, mdl.GithubRepositoryReadProgress())
        return read_progress.issues_event_read_marker

    async def _process_events(
            self, repo: gh_repo.Repository,
            issue_events: list[gh_iss.IssueEvent]) -> None:
        issue_stati = await asyncio.to_thread(
            self._issue_state_changes, issue_events)
        read_marker = self._get_read_marker(repo.full_name)
        for issue_event in issue_events:
            messages: list[mdl.IncomingGithubMessage] = []
            assigned, event_id = issue_stati[issue_event.issue.number]
            if issue_event.id == event_id:
                # This is the issue_event of assignment state change.
                messages += await asyncio.to_thread(
                    self._incoming_messages_for_assignment_change, repo,
                    issue_event, assigned)
            elif assigned:
                messages += await asyncio.to_thread(
                    self._incoming_messages_for_event, repo, issue_event)
            for message in messages:
                self._state.unread_messages.setdefault(
                    message.chat.chat_id, []).append(message)
                await self._publisher.append(message)
            read_progress = self._state.repo_read_progress[repo.full_name]
            read_progress.issues_event_read_marker = (
                self._updated_issues_event_read_marker(
                    read_marker, issue_event))

    def _updated_issues_event_read_marker(
            self, read_marker: mdl.GithubEventReadMarker,
            issue_event: gh_iss.IssueEvent) -> mdl.GithubEventReadMarker:
        """
        Update a read marker with an event.

        Returns a read marker that marks the given event as read. This may be a
        new instance, or the original one with the event's ID added to the set.
        """
        this_event_time = we.Instant(issue_event.created_at)
        if this_event_time > read_marker.last_event_time:
            read_marker = mdl.GithubEventReadMarker(
                last_event_time=this_event_time,
                last_event_ids={issue_event.id})
        elif this_event_time == read_marker.last_event_time:
            read_marker.last_event_ids.add(issue_event.id)
        return read_marker

    def _issue_state_changes(
        self, issue_events: list[gh_iss.IssueEvent]
    ) -> dict[int, tuple[bool, int | None]]:
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
            assigned: bool) -> list[mdl.IncomingGithubMessage]:
        chat = self._make_chat_descriptor(repo, issue_event)
        if not assigned:
            content = self._message_templates["unassigned"].render(
                chat=chat.model_dump_json(),
                issue_number=issue_event.issue.number,
                actor_login=issue_event.actor.login,
                label_name=issue_event.label.name)
            return [
                self._make_system_message(
                    repo, issue_event, content, chat=chat)]
        assignment_message_content = (
            self._message_templates["assigned"].render(
                chat=chat.model_dump_json(),
                issue_number=issue_event.issue.number,
                actor_login=issue_event.actor.login,
                label_name=issue_event.label.name,
                num_messages=issue_event.issue.comments))
        messages: list[mdl.IncomingGithubMessage] = []
        messages.append(
            self._make_system_message(
                repo, issue_event, assignment_message_content, chat=chat))
        description_content = issue_event.issue.body
        if not description_content:
            description_content = "No description provided."
        messages.append(
            self._make_user_message(
                repo, issue_event, "description", issue_event.created_at,
                description_content, chat=chat))
        if issue_event.issue.comments:
            for comment in issue_event.issue.get_comments():
                messages.append(
                    self._make_user_message(
                        repo, issue_event, "comment", comment.created_at,
                        comment.body, chat=chat))
        return messages

    def _make_chat_descriptor(
            self, repo: gh_repo.Repository,
            issue_event: gh_iss.IssueEvent) -> mdl.GithubChatDescriptor:
        return mdl.GithubChatDescriptor(
            chat_id=mdl.GithubChatDescriptor.create_chat_id(
                "issue", repo.full_name, issue_event.issue.number),
            issue_type="issue",
            repo_full_name=repo.full_name,
            issue_number=issue_event.issue.number,
            issue_title=issue_event.issue.title,
            issue_author=issue_event.issue.user.login,
        )

    def _make_system_message(
        self, repo: gh_repo.Repository, issue_event: gh_iss.IssueEvent,
        content: str, chat: mdl.GithubChatDescriptor | None = None
    ) -> mdl.IncomingGithubMessage:
        chat = chat or self._make_chat_descriptor(repo, issue_event)
        system_message = mdl.SystemMessage(
            metadata=mdl.InternalMessageMetadata(
                time=we.Instant(issue_event.created_at)), content=content)
        return mdl.IncomingGithubMessage(
            chat=chat, message=system_message, event_id=issue_event.id)

    def _make_user_message(
        self, repo: gh_repo.Repository, issue_event: gh_iss.IssueEvent,
        comment_type: t.Literal["description",
                                "comment"], created_at: dt.datetime,
        content: str, chat: mdl.GithubChatDescriptor | None = None
    ) -> mdl.IncomingGithubMessage:
        chat = chat or self._make_chat_descriptor(repo, issue_event)
        user_message = mdl.GithubChatMessage(
            role="user", metadata=mdl.GithubChatMessageMetadata(
                time=we.Instant(created_at), chat=chat,
                comment_type=comment_type), content=content)
        return mdl.IncomingGithubMessage(
            chat=chat, message=user_message, event_id=issue_event.id)

    def _incoming_messages_for_event(
            self, repo: gh_repo.Repository,
            issue_event: gh_iss.IssueEvent) -> list[mdl.IncomingGithubMessage]:
        return []
