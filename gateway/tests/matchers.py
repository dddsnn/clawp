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

import json

import hamcrest.core.base_matcher


class JsonEquivalentMatcher(hamcrest.core.base_matcher.BaseMatcher):
    def __init__(self, json_data):
        self.json_data = json_data

    def _matches(self, item):
        try:
            return self.json_data == json.loads(item)
        except json.JSONDecodeError:
            return False

    def describe_to(self, description):
        description.append_text(
            "string parsing to JSON"
        ).append_description_of(self.json_data)


def json_equivalent(json_data):
    return JsonEquivalentMatcher(json_data)
