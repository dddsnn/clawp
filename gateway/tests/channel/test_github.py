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
import yarl
from hamcrest import (
    assert_that,
    contains_exactly,
    has_entries,
    has_properties,
)

import clawp.util
from clawp.channel import github


def get_request_with_headers(headers: dict[str, str]):
    return has_properties(method="GET", headers=has_entries(**headers))


class TestProgressChecker:
    @pytest.fixture
    def token(self):
        return "test-token"

    @pytest.fixture
    async def mock_github_client(self, token):
        mock_github_client = um.Mock(spec=github.GithubAppClient)
        mock_github_client.installation_token = clawp.util.create_done_future(
            token)
        return mock_github_client


class TestFirstPageProgressChecker(TestProgressChecker):
    CHECK_URL = yarl.URL("https://example.org/endpoint?param=2")
    FIRST_PAGE_URL = str(CHECK_URL.update_query(per_page=1, page=1))

    @pytest.fixture
    async def checker(self, mock_github_client):
        httpx_client = httpx.AsyncClient()
        checker = github.ProgressChecker(
            mock_github_client, httpx_client, self.CHECK_URL,
            look_for_changes_in="first_page")
        yield checker
        await httpx_client.aclose()

    async def test_has_changes_raises_if_not_active(self, checker):
        with pytest.raises(ValueError):
            _ = checker.has_changes

    async def test_sets_headers(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock, token):
        httpx_mock.add_response(url=self.FIRST_PAGE_URL, status_code=200)
        assert httpx_mock.get_requests() == []
        async with checker:
            assert_that(
                httpx_mock.get_requests(),
                contains_exactly(
                    get_request_with_headers({
                        "authorization": f"Bearer {token}",
                        "accept": "application/vnd.github+json"})))

    async def test_has_changes_if_200(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.FIRST_PAGE_URL, status_code=200)
        async with checker:
            assert checker.has_changes

    async def test_provides_previous_etag(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with checker:
            pass
        async with checker:
            pass

    async def test_no_changes_if_304(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_has_changes_if_repeated_200(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_provides_new_etag_after_multiple_responses(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag2"'},
            headers={"etag": '"tag3"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_doesnt_update_etag_on_exception_in_cm(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag2"'}, status_code=200)
        async with checker:
            assert checker.has_changes
        with pytest.raises(RuntimeError):
            async with checker:
                assert checker.has_changes
                raise RuntimeError
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_unexpected_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.FIRST_PAGE_URL, status_code=500)
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_exception(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, match_headers={"If-None-Match": '"tag1"'},
            headers={"etag": '"tag1"'}, status_code=304)
        httpx_mock.add_exception(httpx.ReadTimeout(""))
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_missing_etag_in_response(
            self, checker, httpx_mock: pytest_httpx.HTTPXMock):
        httpx_mock.add_response(url=self.FIRST_PAGE_URL, status_code=200)
        httpx_mock.add_response(
            url=self.FIRST_PAGE_URL, headers={"etag": '"tag1"'},
            status_code=200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes


class TestLastPageProgressChecker(TestProgressChecker):
    CHECK_URL = yarl.URL("https://example.org/endpoint?param=2")
    PER_PAGE = 100

    @pytest.fixture
    async def checker(self, mock_github_client):
        httpx_client = httpx.AsyncClient()
        checker = github.ProgressChecker(
            mock_github_client, httpx_client, self.CHECK_URL,
            look_for_changes_in="last_page")
        yield checker
        await httpx_client.aclose()

    @pytest.fixture
    async def add_page_response(self, httpx_mock: pytest_httpx.HTTPXMock):
        def adder(
                page_number, num_elements, etag, match_if_none_match,
                status_code):
            assert page_number > 0
            assert 0 <= num_elements <= self.PER_PAGE
            page_url = self.CHECK_URL.update_query(
                per_page=self.PER_PAGE, page=page_number)
            kwargs = {}
            if match_if_none_match is not None:
                kwargs["match_headers"] = {
                    "If-None-Match": match_if_none_match}
            if num_elements == 0:
                assert status_code == 404
                httpx_mock.add_response(
                    url=str(page_url), status_code=404, **kwargs)
                return
            if etag is not None:
                kwargs["headers"] = {"etag": etag}
            if status_code == 200:
                kwargs["json"] = [{
                    "page_number": page_number, "item_index": i}
                                  for i in range(num_elements)]
            httpx_mock.add_response(
                url=str(page_url), status_code=status_code, **kwargs)

        return adder

    async def test_has_changes_raises_if_not_active(self, checker):
        with pytest.raises(ValueError):
            _ = checker.has_changes

    async def test_sets_headers(
            self, checker, add_page_response,
            httpx_mock: pytest_httpx.HTTPXMock, token):
        add_page_response(1, 1, '"tag1"', None, 200)
        assert httpx_mock.get_requests() == []
        async with checker:
            assert_that(
                httpx_mock.get_requests(),
                contains_exactly(
                    get_request_with_headers({
                        "authorization": f"Bearer {token}",
                        "accept": "application/vnd.github+json"})))

    async def test_has_changes_if_404_with_empty_first_page(
            self, checker, add_page_response):
        add_page_response(1, 0, '"tag1"', None, 404)
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_200_on_partial_first_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE - 1, '"tag1"', None, 200)
        async with checker:
            assert checker.has_changes

    async def test_no_changes_if_304_on_partial_first_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE - 1, '"tag1"', None, 200)
        add_page_response(1, self.PER_PAGE - 1, '"tag1"', '"tag1"', 304)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_has_changes_if_repeated_200_on_partial_first_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE - 2, '"tag1"', None, 200)
        add_page_response(1, self.PER_PAGE - 1, '"tag2"', '"tag1"', 200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_200_on_partial_successive_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag2"', None, 200)
        async with checker:
            assert checker.has_changes

    async def test_no_changes_if_304_on_partial_successive_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag2"', '"tag2"', 304)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_has_changes_if_repeated_200_on_partial_successive_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag3"', '"tag2"', 200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_200_with_following_empty_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, 0, None, None, 404)
        async with checker:
            assert checker.has_changes

    async def test_no_changes_if_304_with_following_empty_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, 0, None, None, 404)
        add_page_response(1, self.PER_PAGE, '"tag1"', '"tag1"', 304)
        add_page_response(2, 0, None, None, 404)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_has_changes_if_previously_empty_page_has_content(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, 0, None, None, 404)
        add_page_response(1, self.PER_PAGE, '"tag1"', '"tag1"', 304)
        add_page_response(2, 5, '"tag2"', None, 200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_no_changes_if_304_in_previously_empty_page(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, 0, None, None, 404)
        add_page_response(1, self.PER_PAGE, '"tag1"', '"tag1"', 304)
        add_page_response(2, 5, '"tag2"', None, 200)
        add_page_response(2, 5, '"tag2"', '"tag2"', 304)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes

    async def test_provides_new_etag_after_multiple_responses(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 3, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 2, '"tag3"', '"tag2"', 200)
        add_page_response(2, self.PER_PAGE - 1, '"tag4"', '"tag3"', 200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_doesnt_update_etag_on_exception_in_cm(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 3, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 2, '"tag3"', '"tag2"', 200)
        add_page_response(2, self.PER_PAGE - 2, '"tag3"', '"tag2"', 200)
        async with checker:
            assert checker.has_changes
        with pytest.raises(RuntimeError):
            async with checker:
                assert checker.has_changes
                raise RuntimeError
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_unexpected_response(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 3, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 3, '"tag2"', '"tag2"', 500)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_exception(
            self, checker, add_page_response,
            httpx_mock: pytest_httpx.HTTPXMock):
        add_page_response(1, self.PER_PAGE, '"tag1"', None, 200)
        add_page_response(2, self.PER_PAGE - 2, '"tag2"', None, 200)
        add_page_response(2, self.PER_PAGE - 2, '"tag2"', '"tag2"', 304)
        httpx_mock.add_exception(httpx.ReadTimeout(""))
        async with checker:
            assert checker.has_changes
        async with checker:
            assert not checker.has_changes
        async with checker:
            assert checker.has_changes

    async def test_has_changes_if_missing_etag_in_response(
            self, checker, add_page_response):
        add_page_response(1, self.PER_PAGE, None, None, 200)
        add_page_response(2, self.PER_PAGE - 2, None, None, 200)
        add_page_response(2, self.PER_PAGE - 2, None, None, 200)
        async with checker:
            assert checker.has_changes
        async with checker:
            assert checker.has_changes


class TestProgressCheckers:
    @pytest.fixture(autouse=True)
    def mock_checker(self, monkeypatch):
        def constructor(_1, _2, check_url, *, look_for_changes_in):
            return um.Mock(
                check_url=check_url, look_for_changes_in=look_for_changes_in)

        monkeypatch.setattr(
            github, "ProgressChecker", um.Mock(side_effect=constructor))
        return github.ProgressChecker

    @pytest.mark.parametrize(
        "look_for_changes_in", ["first_page", "last_page"])
    def test_creates_new_checker(self, mock_checker, look_for_changes_in):
        client = um.Mock()
        checkers = github.ProgressCheckers(client)
        mock_checker.assert_not_called()
        checker = checkers.for_url(
            yarl.URL("https://example.org/endpoint"),
            look_for_changes_in=look_for_changes_in)
        mock_checker.assert_called_once_with(
            client, um.ANY, yarl.URL("https://example.org/endpoint"),
            look_for_changes_in=look_for_changes_in)
        assert checker.check_url == yarl.URL("https://example.org/endpoint")

    def test_uses_existing_checker_for_same_url(self, mock_checker):
        client = um.Mock()
        checkers = github.ProgressCheckers(client)
        mock_checker.assert_not_called()
        checker1 = checkers.for_url(
            yarl.URL("https://example.org/endpoint"),
            look_for_changes_in="first_page")
        checker2 = checkers.for_url(
            yarl.URL("https://example.org/endpoint"),
            look_for_changes_in="first_page")
        mock_checker.assert_called_once_with(
            client, um.ANY, yarl.URL("https://example.org/endpoint"),
            look_for_changes_in="first_page")
        assert checker1 is checker2

    def test_creates_new_checker_for_different_url(self, mock_checker):
        client = um.Mock()
        checkers = github.ProgressCheckers(client)
        mock_checker.assert_not_called()
        checker1 = checkers.for_url(
            yarl.URL("https://example.org/endpoint1"),
            look_for_changes_in="first_page")
        checker2 = checkers.for_url(
            yarl.URL("https://example.org/endpoint2"),
            look_for_changes_in="first_page")
        assert mock_checker.call_args_list == [
            um.call(
                client, um.ANY, yarl.URL("https://example.org/endpoint1"),
                look_for_changes_in="first_page"),
            um.call(
                client, um.ANY, yarl.URL("https://example.org/endpoint2"),
                look_for_changes_in="first_page"),]
        assert checker1 is not checker2

    def test_creates_new_checker_for_different_mode(self, mock_checker):
        client = um.Mock()
        checkers = github.ProgressCheckers(client)
        mock_checker.assert_not_called()
        checker1 = checkers.for_url(
            yarl.URL("https://example.org/endpoint"),
            look_for_changes_in="first_page")
        checker2 = checkers.for_url(
            yarl.URL("https://example.org/endpoint"),
            look_for_changes_in="last_page")
        assert mock_checker.call_args_list == [
            um.call(
                client, um.ANY, yarl.URL("https://example.org/endpoint"),
                look_for_changes_in="first_page"),
            um.call(
                client, um.ANY, yarl.URL("https://example.org/endpoint"),
                look_for_changes_in="last_page"),]
        assert checker1 is not checker2
