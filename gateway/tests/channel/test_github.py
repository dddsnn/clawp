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

import unittest.mock as um

import httpx
import pytest
import pytest_httpx
from hamcrest import (
    assert_that,
    contains_exactly,
    has_entries,
    has_properties,
)

from clawp import model as mdl
from clawp.channel import github


def get_request_with_headers(headers: dict[str, str]):
    return has_properties(method="GET", headers=has_entries(**headers))


class TestRepositoryProgressChecker:
    REPO_FULL_NAME = "owner-name/repo-name"
    ISSUES_EVENTS_URL = (
        f"https://api.github.com/repos/{REPO_FULL_NAME}/issues/events")

    @pytest.fixture
    def token(self):
        return "Bearer test-token"

    @pytest.fixture
    def mock_github_client(self, token):
        mock_github_client = um.Mock(spec=github.GithubAppClient)
        mock_github_client.get_installation_token.return_value = token
        return mock_github_client

    @pytest.fixture
    def make_repo_checker(self, mock_github_client):
        def factory(read_progress):
            httpx_client = httpx.AsyncClient()
            repo_checker = github.RepositoryProgressChecker(
                mock_github_client, read_progress, self.REPO_FULL_NAME,
                httpx_client)
            return repo_checker, httpx_client

        return factory

    @pytest.fixture
    async def repo_checker(self, make_repo_checker):
        repo_checker, httpx_client = make_repo_checker(
            mdl.GithubRepositoryReadProgress())
        yield repo_checker
        await httpx_client.aclose()

    async def test_issues_events_raises_if_not_active(self, repo_checker):
        with pytest.raises(ValueError):
            _ = repo_checker.new_issues_events_available

    async def test_sets_headers(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock, token):
        httpx_mock.add_response(url=self.ISSUES_EVENTS_URL, status_code=200)
        assert httpx_mock.get_requests() == []
        async with repo_checker:
            assert_that(
                httpx_mock.get_requests(),
                contains_exactly(
                    get_request_with_headers({
                        "authorization": token,
                        "accept": "application/vnd.github+json"})))

    async def test_issues_events_available_if_200_response(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.ISSUES_EVENTS_URL, status_code=200)
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_provides_previous_etag(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with repo_checker:
            pass
        async with repo_checker:
            pass

    async def test_no_issues_events_available_if_304_response(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert not repo_checker.new_issues_events_available

    async def test_issues_events_available_if_repeated_200_response(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_provides_new_etag_after_multiple_responses(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag2"'},
            headers={"etag": '"tag3"'}, status_code=200)
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_doesnt_update_etag_on_exception_in_context_manager(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        with pytest.raises(RuntimeError):
            async with repo_checker:
                assert repo_checker.new_issues_events_available
                raise RuntimeError
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_issues_events_available_if_unexpected_response(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.ISSUES_EVENTS_URL, status_code=500)
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_issues_events_available_if_exception(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        httpx_mock.add_exception(httpx.ReadTimeout(""))
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert not repo_checker.new_issues_events_available
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_issues_events_available_if_missing_etag_in_response(
            self, repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.ISSUES_EVENTS_URL, status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        async with repo_checker:
            assert repo_checker.new_issues_events_available
        async with repo_checker:
            assert repo_checker.new_issues_events_available

    async def test_persists_previous_etag(
            self, make_repo_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.ISSUES_EVENTS_URL,
            match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        read_progress = mdl.GithubRepositoryReadProgress()
        repo_checker, httpx_client = make_repo_checker(read_progress)
        async with repo_checker:
            pass
        await httpx_client.aclose()
        repo_checker, httpx_client = make_repo_checker(read_progress)
        async with repo_checker:
            pass
        await httpx_client.aclose()


class TestProgressChecker:
    @pytest.fixture
    def mock_repo_checker(self, monkeypatch):
        monkeypatch.setattr(github, "RepositoryProgressChecker", um.Mock())
        return github.RepositoryProgressChecker

    def test_creates_new_progress(self, mock_repo_checker):
        client, state = um.Mock(), mdl.GithubChannelState()
        checker = github.ProgressChecker(client, state)
        mock_repo_checker.assert_not_called()
        checker.for_repo("repo1")
        mock_repo_checker.assert_called_once_with(
            client, state.repo_read_progress["repo1"], "repo1", um.ANY)

    def test_uses_existing_progress(self, mock_repo_checker):
        client, state = um.Mock(), mdl.GithubChannelState()
        checker = github.ProgressChecker(client, state)
        checker.for_repo("repo1")
        progress = state.repo_read_progress["repo1"]
        checker.for_repo("repo1")
        assert state.repo_read_progress["repo1"] is progress
        assert mock_repo_checker.call_args_list == [
            um.call(client, progress, "repo1", um.ANY),
            um.call(client, progress, "repo1", um.ANY)]
