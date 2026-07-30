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
import itertools as it
import logging
import operator as op
import typing as t

import github
import github.Issue as gh_iss
import github.IssueEvent as gh_issev
import github.Repository as gh_repo
import github.TimelineEvent as gh_tl
import httpx
import pydantic as pyd
import whenever as we
import yarl

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


class ProgressChecker:
    """
    Utility checking whether there is any progress in the API.

    This is a context manager that makes requests to paginated Github API
    endpoints making use of ETag/If-None-Match headers. Paginated endpoints are
    ones that support the page and per_page query parameters. The has_changes
    property is available when the context manager is active, indicating
    whether a request to the endpoint would yield new results.

    The ETag value of any response is persisted when the context manager exits
    and provided as If-None-Match header on the next request. Checks the
    response's status code, where 200 means there are new events, 304 means no
    new events.

    The checker operates in one of two modes, depending on the
    look_for_changes_in parameter: It can look for changes in the first page of
    the response. This is the easy case, in which it only fetches the first
    item from the first page of the endpoint. This is meant for endpoints that
    are sorted newest-first, where any change will necessarily change the first
    item.

    The more complex case is last_page, meant for endpoints that are sorted
    such that new items are at the end, where a new item might not change the
    first page at all. In this mode, the checker uses the maximum allowable
    page size to load all pages once, record their ETags and learn how many
    pages there are. On subsequent checks, it only checks the last page. If the
    the page is not full and the status code is 304, there are no changes. If
    the page is full (i.e. maximum number of items) the next page is checked.
    If the next page doesn't exist (indicated by a 404 response), there are no
    changes. All other conditions lead the checker to report that there are
    changes.

    If an exception occurs when querying the API, defaults to indicating that
    there are changes (so that nothing is skipped accidentally). The same
    applies when an exception occurs within the context manager (i.e. the code
    wrapped by it). This implies that client code must be ready to receive
    events it has seen before and filter them appropriately.

    This only works as intended if the client code processes all events within
    the context manager. Once the context manager exits, it is asssumed they
    never need to be received again. If an error occurs in the processing code,
    an exception must be raised in the context manager so that the events are
    not marked as seen and can be tried again.

    The context manager is reusable but not reentrant.
    """
    GITHUB_MAX_PER_PAGE = 100

    @dc.dataclass
    class PageStatus:
        etag: str | None
        page_is_full: bool

    def __init__(
            self, github_client: GithubAppClient,
            httpx_client: httpx.AsyncClient, check_url: yarl.URL, *,
            look_for_changes_in: t.Literal["first_page", "last_page"]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._github_client = github_client
        self._httpx_client = httpx_client
        self._check_url = check_url
        if look_for_changes_in == "first_page":
            self._per_page = 1
        else:
            self._per_page = self.GITHUB_MAX_PER_PAGE
        self._read_pages = {}
        self._active_pages = {}
        self._has_changes = None

    async def __aenter__(self) -> t.Self:
        assert self._has_changes is None
        self._active_pages = {}
        await self._update()
        assert self._has_changes is not None
        return self

    async def __aexit__(
            self, exc_type: type[BaseException] | None, *_) -> bool:
        self._has_changes = None
        for page_number, page_status in self._active_pages.items():
            if page_status.etag is None:
                self._logger.warning(
                    f"{self._check_url} responded without an ETag on page "
                    f"{page_number}.")
        if exc_type is None:
            self._read_pages |= self._active_pages
        else:
            self._logger.debug(
                f"Discarding ETags because of exception in context manager "
                f"processing results from {self._check_url}.")
        return False

    async def _update(self) -> None:
        try:
            await self._update_pages()
        except Exception:
            self._logger.exception(
                "Error checking ETags, setting changes flag.")
            self._has_changes = True

    async def _update_pages(self,) -> None:
        # Start with the last page we know.
        start_page = max(self._read_pages.keys(), default=1)
        for page in it.count(start=start_page):
            page_status = self._read_pages.get(
                page, self.PageStatus(etag=None, page_is_full=False))
            response = await self._get_page(page, page_status.etag)
            if response.status_code not in [200, 304, 404]:
                self._logger.warning(
                    f"Unexpected status {response.status_code} in response "
                    f"from {self._check_url}.")
            if response.status_code == 404:
                if self._has_changes is None:
                    # The page doesn't exist. If it's the first page (i.e. we
                    # haven't decided whether there are changes), we should
                    # report that there are changes.
                    self._has_changes = True
                # Break before we even record a status for the missing page.
                break
            # 304 response -> no changes.
            self._has_changes = response.status_code != 304
            if response.status_code == 200:
                # For first_page mode, page_is_full is always False, i.e. we
                # always break out of the loop after the first iteration.
                page_is_full = (
                    len(response.json()) == self.GITHUB_MAX_PER_PAGE)
            else:
                page_is_full = page_status.page_is_full
            self._active_pages[page] = self.PageStatus(
                etag=response.headers.get("ETag"), page_is_full=page_is_full)
            if not page_is_full:
                # Only continue checking if the page is full and we need to see
                # what comes after.
                break

    async def _get_page(self, page: int, etag: str | None) -> httpx.Response:
        headers = {
            "Authorization": "Bearer " +
            (await self._github_client.installation_token),
            "Accept": "application/vnd.github+json"}
        if etag is not None:
            headers["If-None-Match"] = etag
        return await self._httpx_client.get(
            self._page_url(page), headers=headers)

    def _page_url(self, page: int) -> str:
        return str(
            self._check_url.update_query(page=page, per_page=self._per_page))

    @property
    def has_changes(self) -> bool:
        """
        Whether there are any changes.

        Indicates whether a query to endpoint will return results that didn't
        exist since the last time the context manager exited.

        The context manager must be active or this raises an exception.
        """
        if self._has_changes is None:
            raise ValueError("context manager is not active")
        return self._has_changes


class ProgressCheckers:
    """
    Utility checking whether there is any progress in the API.

    This is just a manager for the specific checkers that manages their state
    and a shared http client.

    The checker should be closed with aclose() on shutdown.
    """
    def __init__(self, github_client: GithubAppClient) -> None:
        self._github_client = github_client
        self._httpx_client = httpx.AsyncClient()
        self._checkers = {}

    async def aclose(self) -> None:
        await self._httpx_client.aclose()

    def for_url(
        self, check_url: yarl.URL, *,
        look_for_changes_in: t.Literal["first_page", "last_page"]
    ) -> ProgressChecker:
        """
        Get a checker for a URL.

        Creates or returns an existing checker for the given URL. The checker
        can have one of the 2 modes looking for changes in the first or the
        last page. See the ProgressChecker documentation for details.

        Creates a new read progress state for the repo if necessary.
        """
        key = (check_url, look_for_changes_in)
        try:
            return self._checkers[key]
        except KeyError:
            return self._checkers.setdefault(
                key,
                ProgressChecker(
                    self._github_client, self._httpx_client, check_url,
                    look_for_changes_in=look_for_changes_in))


@dc.dataclass
class Event:
    event: gh_issev.IssueEvent | gh_tl.TimelineEvent
    issue: gh_iss.Issue
    time: we.Instant


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
    _RELEVANT_TIMELINE_ISSUE_EVENTS = ["commented"]

    def __init__(
            self, config: mdl.GithubAccountConfig,
            state: mdl.GithubChannelState) -> None:
        super().__init__("github")
        self._config = config
        self._state = state
        self._client = GithubAppClient(self._config)
        self._progress_checkers = ProgressCheckers(self._client)
        self._poll_task: asyncio.Task | None = None
        self._message_templates: dict[str, file.Template] = None
        self._assigned_issues: dict[str, list[gh_iss.Issue]] = {}

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        self._message_templates = {
            "assigned": await file.read_message_template(
                "system_information/github_assigned.md"),
            "unassigned": await file.read_message_template(
                "system_information/github_unassigned.md"),}
        await self._client.__aenter__()
        await self._ensure_assigned_issues()
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
        await self._progress_checkers.aclose()
        return await super().__aexit__(*args)

    async def _ensure_assigned_issues(self) -> None:
        for repo in await self._client.list_installation_repositories():
            async with self._assigned_issues_checker(
                    repo.full_name) as assigned_issues_checker:
                should_get_issues = (
                    repo.full_name not in self._assigned_issues
                    or assigned_issues_checker.has_changes)
                if not should_get_issues:
                    continue
                self._logger.debug(
                    f"Getting assigned issues for {repo.full_name}.")
                self._assigned_issues[repo.full_name] = (
                    await self._read_paginated_list(
                        repo.get_issues, labels=[self.agent_assigned_label]))

    async def _read_paginated_list(self, list_getter, *args, **kwargs) -> list:
        paginated_list = await asyncio.to_thread(list_getter, *args, **kwargs)
        return await asyncio.to_thread(list, paginated_list)

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

    async def get_unread_messages(self,
                                  chat_id: str) -> list[mdl.IncomingMessage]:
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

    def _issues_events_url(self, repo_full_name: str) -> yarl.URL:
        return yarl.URL(
            f"https://api.github.com/repos/{repo_full_name}/issues/events")

    def _assigned_issues_url(self, repo_full_name: str) -> yarl.URL:
        return yarl.URL(
            f"https://api.github.com/repos/{repo_full_name}/issues"
            f"?labels={self.agent_assigned_label}")

    def _issue_timeline_url(
            self, repo_full_name: str, issue_number: int) -> yarl.URL:
        return yarl.URL(
            f"https://api.github.com/repos/{repo_full_name}/issues"
            f"/{issue_number}/timeline")

    def _issues_events_checker(self, repo_full_name: str) -> ProgressChecker:
        # Issues events are sorted newest first, so look on the first page.
        return self._progress_checkers.for_url(
            self._issues_events_url(repo_full_name),
            look_for_changes_in="first_page")

    def _assigned_issues_checker(self, repo_full_name: str) -> ProgressChecker:
        # For the issues endpoint, sort issues so we get the last updated one
        # first, and then we only have to check the first page.
        check_url = self._assigned_issues_url(repo_full_name)
        check_url.update_query(sort="updated", direction="desc")
        return self._progress_checkers.for_url(
            check_url, look_for_changes_in="first_page")

    def _issue_timeline_checker(
            self, repo_full_name: str, issue_number: int) -> ProgressChecker:
        # The issue timeline endpoint is sorted with the newest event at the
        # end (and has no option to sort in another way). Check the last page
        # for changes.
        return self._progress_checkers.for_url(
            self._issue_timeline_url(repo_full_name, issue_number),
            look_for_changes_in="last_page")

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
        async with contextlib.AsyncExitStack() as stack:
            issues_events_checker = await stack.enter_async_context(
                self._issues_events_checker(repo.full_name))
            events: list[Event] = []
            if issues_events_checker.has_changes:
                events += await asyncio.to_thread(
                    self._get_relevant_new_issue_events, repo)
                self._logger.debug(
                    f"Got {len(events)} new issue events polling repository "
                    f"{repo.full_name}.")
            await self._ensure_assigned_issues()
            assert repo.full_name in self._assigned_issues
            for issue in self._assigned_issues[repo.full_name]:
                issue_timeline_checker = await stack.enter_async_context(
                    self._issue_timeline_checker(repo.full_name, issue.number))
                if issue_timeline_checker.has_changes:
                    events += await asyncio.to_thread(
                        self._get_relevant_new_timeline_events, repo, issue)
            new_events = sorted(events, key=op.attrgetter("time"))
            if new_events:
                await self._process_events(repo, new_events)
            # After successful processing, all checker context managers exit
            # and commit their etags.

    def _get_relevant_new_issue_events(
            self, repo: gh_repo.Repository) -> list[Event]:
        read_marker = self._state.read_markers.setdefault(
            self._issues_events_url(repo.full_name),
            mdl.GithubEventReadMarker.min())
        new_events: list[Event] = []
        for issue_event in repo.get_issues_events():
            # The issue events are sorted newest-first, so collect them until
            # we come across the first event we've already seen.
            this_event_time = we.Instant(issue_event.created_at)
            already_seen = (
                this_event_time < read_marker.last_event_time
                or issue_event.node_id in read_marker.last_event_ids)
            if already_seen:
                break
            if issue_event.event in self._RELEVANT_ISSUE_EVENTS:
                event = Event(
                    event=issue_event, issue=issue_event.issue,
                    time=this_event_time)
                new_events.append(event)
        return new_events

    def _get_relevant_new_timeline_events(
            self, repo: gh_repo.Repository,
            issue: gh_iss.Issue) -> list[Event]:
        read_marker = self._state.read_markers.setdefault(
            self._issue_timeline_url(repo.full_name, issue.number),
            mdl.GithubEventReadMarker.min())
        new_events: list[Event] = []
        for timeline_event in reversed(issue.get_timeline()):
            # Issue timeline events are sorted in chronological order (oldest
            # first), so iterate in reverse and collect them until we come
            # across the first event we've already seen.
            if timeline_event.id is None:
                self._logger.warning(
                    f"Skipping timeline event {timeline_event} "
                    f"({timeline_event.event}) without an ID.")
                continue
            this_event_time = we.Instant(timeline_event.created_at)
            already_seen = (
                this_event_time < read_marker.last_event_time
                or timeline_event.node_id in read_marker.last_event_ids)
            if already_seen:
                break
            if timeline_event.event in self._RELEVANT_TIMELINE_ISSUE_EVENTS:
                event = Event(
                    event=timeline_event, issue=issue, time=this_event_time)
                new_events.append(event)
        return new_events

    async def _process_events(
            self, repo: gh_repo.Repository, events: list[Event]) -> None:
        issue_stati = self._issue_state_changes(events)
        for event in events:
            messages: list[mdl.IncomingMessage] = []
            if isinstance(event.event, gh_issev.IssueEvent):
                read_marker_key = self._issues_events_url(repo.full_name)
                assigned, event_id = issue_stati[event.issue.number]
                if event.event.id == event_id:
                    # This is the event of assignment state change.
                    messages += await asyncio.to_thread(
                        self._incoming_messages_for_assignment_change, repo,
                        event, assigned)
            else:
                assert isinstance(event.event, gh_tl.TimelineEvent)
                read_marker_key = self._issue_timeline_url(
                    repo.full_name, event.issue.number)
                messages += await asyncio.to_thread(
                    self._incoming_messages_for_timeline_event, repo, event)
            for message in messages:
                self._state.unread_messages.setdefault(
                    message.chat.chat_id, []).append(message)
                await self._publisher.append(message)
            self._update_event_read_marker(read_marker_key, event)

    def _update_event_read_marker(
            self, endpoint_url: yarl.URL, event: Event) -> None:
        """
        Update a read marker with an event.

        Updates the read marker keyed on the given endpoint URL so that it
        marks the given event as read. This will probably be a new instance,
        but may also be the original one with the event's node ID added to the
        set.
        """
        read_marker = self._state.read_markers.setdefault(
            endpoint_url, mdl.GithubEventReadMarker.min())
        if event.time > read_marker.last_event_time:
            read_marker = mdl.GithubEventReadMarker(
                last_event_time=event.time,
                last_event_ids={event.event.node_id})
        elif event.time == read_marker.last_event_time:
            read_marker.last_event_ids.add(event.event.node_id)
        self._state.read_markers[endpoint_url] = read_marker

    def _issue_state_changes(
            self, events: list[Event]) -> dict[int, tuple[bool, int | None]]:
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
        for event in events:
            if not isinstance(event.event, gh_issev.IssueEvent):
                continue
            issue_event = event.event
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
            self, repo: gh_repo.Repository, event: Event,
            assigned: bool) -> list[mdl.IncomingMessage]:
        assert isinstance(event.event, gh_issev.IssueEvent)
        chat = self._make_chat_descriptor(repo, event.issue)
        if not assigned:
            content = self._message_templates["unassigned"].render(
                chat=chat.model_dump_json(), issue_number=event.issue.number,
                actor_login=event.event.actor.login,
                label_name=event.event.label.name)
            return [self._make_system_message(chat, event.time, content)]
        assignment_message_content = (
            self._message_templates["assigned"].render(
                chat=chat.model_dump_json(), issue_number=event.issue.number,
                actor_login=event.event.actor.login,
                label_name=event.event.label.name,
                num_messages=event.issue.comments))
        messages: list[mdl.IncomingMessage] = []
        messages.append(
            self._make_system_message(
                chat, event.time, assignment_message_content))
        description_content = event.issue.body
        if not description_content:
            description_content = "No description provided."
        messages.append(
            self._make_user_message(
                chat, "description", event.time, description_content))
        if event.issue.comments:
            for comment in event.issue.get_comments():
                messages.append(
                    self._make_user_message(
                        chat, "comment", we.Instant(comment.created_at),
                        comment.body))
        return messages

    def _make_chat_descriptor(
            self, repo: gh_repo.Repository,
            issue: gh_iss.Issue) -> mdl.GithubChatDescriptor:
        return mdl.GithubChatDescriptor(
            chat_id=mdl.GithubChatDescriptor.create_chat_id(
                "issue", repo.full_name, issue.number),
            issue_type="issue",
            repo_full_name=repo.full_name,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_author=issue.user.login,
        )

    def _make_system_message(
            self, chat: mdl.GithubChatDescriptor, time: we.Instant,
            content: str) -> mdl.IncomingMessage:
        system_message = mdl.SystemMessage(
            metadata=mdl.InternalMessageMetadata(time=time), content=content)
        return mdl.IncomingMessage(chat=chat, message=system_message)

    def _make_user_message(
            self, chat: mdl.GithubChatDescriptor,
            comment_type: t.Literal["description", "comment"],
            time: we.Instant, content: str) -> mdl.IncomingMessage:
        user_message = mdl.GithubChatMessage(
            role="user", metadata=mdl.GithubChatMessageMetadata(
                time=time, chat=chat, comment_type=comment_type),
            content=content)
        return mdl.IncomingMessage(chat=chat, message=user_message)

    def _incoming_messages_for_timeline_event(
            self, repo: gh_repo.Repository,
            event: Event) -> list[mdl.IncomingMessage]:
        assert isinstance(event.event, gh_tl.TimelineEvent)
        assert event.event.event == "commented"
        chat = self._make_chat_descriptor(repo, event.issue)
        return [
            self._make_user_message(
                chat, "comment", event.time, event.event.body)]
