# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from packit.schema import USER_CONFIG_SCHEMA

_SERVICE_CONFIG_SCHEMA_PROPERTIES = {
    "deployment": {"type": "string"},
    "webhook_secret": {"type": "string"},
    "testing_farm_secret": {"type": "string"},
    "validate_webhooks": {"type": "boolean"},
}
_SERVICE_CONFIG_SCHEMA_REQUIRED = ["deployment"]

SERVICE_CONFIG_SCHEMA = USER_CONFIG_SCHEMA.copy()
SERVICE_CONFIG_SCHEMA["properties"].update(_SERVICE_CONFIG_SCHEMA_PROPERTIES)
SERVICE_CONFIG_SCHEMA.setdefault("required", [])
SERVICE_CONFIG_SCHEMA["required"] += _SERVICE_CONFIG_SCHEMA_REQUIRED
