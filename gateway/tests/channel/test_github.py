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

import clawp.util
from clawp import model as mdl
from clawp.channel import github


def get_request_with_headers(headers: dict[str, str]):
    return has_properties(method="GET", headers=has_entries(**headers))


class TestProgressChecker:
    CHECK_URL = "https://example.org/endpoint?param=2"

    @pytest.fixture
    def token(self):
        return "test-token"

    @pytest.fixture
    async def mock_github_client(self, token):
        mock_github_client = um.Mock(spec=github.GithubAppClient)
        mock_github_client.installation_token = clawp.util.create_done_future(
            token)
        return mock_github_client

    @pytest.fixture
    def make_checker(self, mock_github_client):
        def factory(read_progress):
            httpx_client = httpx.AsyncClient()
            checker = github.ProgressChecker(
                mock_github_client, read_progress, httpx_client,
                self.CHECK_URL)
            return checker, httpx_client

        return factory

    @pytest.fixture
    async def checker(self, make_checker):
        checker, httpx_client = make_checker(
            mdl.GithubRepositoryReadProgress())
        yield checker
        await httpx_client.aclose()

    async def test_issues_events_raises_if_not_active(self, checker):
        with pytest.raises(ValueError):
            _ = checker.has_changes

    async def test_sets_headers(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock, token):
        httpx_mock.add_response(url=self.CHECK_URL, status_code=200)
        assert httpx_mock.get_requests() == []
        async with checker:
            assert_that(
                httpx_mock.get_requests(),
                contains_exactly(
                    get_request_with_headers({
                        "authorization": f"Bearer {token}",
                        "accept": "application/vnd.github+json"})))

    async def test_issues_events_available_if_200_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.CHECK_URL, status_code=200)
        async with checker:
            assert checker.has_changes

    async def test_provides_previous_etag(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with checker:
            pass
        async with checker:
            pass

    async def test_no_issues_events_available_if_304_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_issues_events_available_if_repeated_200_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_provides_new_etag_after_multiple_responses(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag2"'},
            headers={"etag": '"tag3"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_doesnt_update_etag_on_exception_in_context_manager(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        with pytest.raises(RuntimeError):
            async with checker:
                assert checker.has_changes
                raise RuntimeError
        async with checker:
            assert checker.has_changes

    async def test_issues_events_available_if_unexpected_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.CHECK_URL, status_code=500)
        async with checker:
            assert checker.has_changes

    async def test_issues_events_available_if_exception(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        httpx_mock.add_exception(httpx.ReadTimeout(""))
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes
            async with checker:
                assert checker.has_changes

    async def test_issues_events_available_if_missing_etag_in_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.CHECK_URL, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_persists_previous_etag(
            self, make_checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.CHECK_URL, headers={"etag": '"tag1"'}, status_code=200)
        httpx_mock.add_response(
            url=self.CHECK_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        read_progress = mdl.GithubRepositoryReadProgress()
        checker, httpx_client = make_checker(read_progress)
        async with checker:
            pass
        await httpx_client.aclose()
        checker, httpx_client = make_checker(read_progress)
        async with checker:
            pass
        await httpx_client.aclose()


class TestProgressCheckers:
    @pytest.fixture
    def mock_repo_checker(self, monkeypatch):
        monkeypatch.setattr(github, "ProgressChecker", um.Mock())
        return github.ProgressChecker

    def test_creates_new_progress(self, mock_repo_checker):
        check_url = (
            "https://api.github.com/repos/repo1/issues/events?per_page=1")
        client, state = um.Mock(), mdl.GithubChannelState()
        checkers = github.ProgressCheckers(client, state)
        mock_repo_checker.assert_not_called()
        checkers.for_issue_events("repo1")
        mock_repo_checker.assert_called_once_with(
            client, state.repo_read_progress["repo1"], um.ANY, check_url)

    def test_uses_existing_progress(self, mock_repo_checker):
        client, state = um.Mock(), mdl.GithubChannelState()
        checkers = github.ProgressCheckers(client, state)
        checkers.for_issue_events("repo1")
        progress = state.repo_read_progress["repo1"]
        checkers.for_issue_events("repo1")
        assert state.repo_read_progress["repo1"] is progress
        assert mock_repo_checker.call_args_list == [
            um.call(client, progress, um.ANY, um.ANY),
            um.call(client, progress, um.ANY, um.ANY)]
