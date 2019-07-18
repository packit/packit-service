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

import logging
from typing import Iterable, Tuple, Dict, Any

from fedora_messaging import api
from fedora_messaging.message import Message
import fedmsg
import requests


logger = logging.getLogger(__name__)


def accept_fed_mes_message_cb(message: Message):
    import ipdb; ipdb.set_trace()
    print(f"{message.topic}: {message.body}")


class Consumerino:
    """
    Consume events from fedmsg
    """

    def __init__(self, url: str = None) -> None:
        # TODO: the url template should be configurable
        self.datagrepper_url = url or (
            "https://apps.fedoraproject.org/datagrepper/id?id={msg_id}&is_raw=true"
        )

    @staticmethod
    def yield_all_messages() -> Iterable[Tuple[str, dict]]:
        logger.info("listening on fedmsg")
        for name, endpoint, topic, msg in fedmsg.tail_messages():
            yield topic, msg

    def fetch_fedmsg_dict(self, msg_id: str) -> Dict[str, Any]:
        """
        Fetch selected message from datagrepper

        :param msg_id: str
        :return: dict, the fedmsg
        """
        logger.debug(f"Processing message: {msg_id}")
        url = self.datagrepper_url.format(msg_id=msg_id)
        response = requests.get(url)
        msg_dict = response.json()
        return msg_dict

    def consume_from_fed_mes(self):
        """
        fedora-messging is written in an async way: callbacks
        """
        queue_name = 'prod.packit-service'
        queues = {
            queue_name: {
                'durable': False,  # Delete the queue on broker restart
                'auto_delete': True,  # Delete the queue when the client terminates
                'exclusive': False,  # Allow multiple simultaneous consumers
                'arguments': {},
            },
        }
        binding = {
            'exchange': 'amq.topic',  # The AMQP exchange to bind our queue to
            'queue': queue_name,  # The unique name of our queue on the AMQP broker
            'routing_keys': ['#'],  # The topics that should be delivered to the queue
        }

        # Start consuming messages using our callback. This call will block until
        # a KeyboardInterrupt is raised, or the process receives a SIGINT or SIGTERM
        # signal.
        import ipdb; ipdb.set_trace()
        api.consume(accept_fed_mes_message_cb, bindings=binding, queues=queues)
