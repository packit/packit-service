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

from fedora_messaging import api, config
from fedora_messaging.message import Message
from packit_service.celerizer import celery_app

config.conf.setup_logging()
logger = logging.getLogger(__name__)


class Consumerino:
    """
    Consume events from fedora messaging
    """

    @staticmethod
    def fedora_messaging_callback(message: Message):
        """
        Create celery task from fedora message
        :param message: Message from Fedora message bus
        :return: None
        """

        if message.body["owner"] != "packit":
            logger.debug("Copr build is not handled by packit!")
            return

        message.body["topic"] = message.topic
        celery_app.send_task(
            name="task.steve_jobs.process_message", kwargs={"event": message.body}
        )

    @staticmethod
    def consume_from_fedora_messaging():
        """
        fedora-messaging is written in an async way: callbacks
        """
        queue_name = "708D1D74-63E4-472A-88E8-8E43C5AE40DC"
        queues = {
            queue_name: {
                "durable": False,  # Delete the queue on broker restart
                "auto_delete": True,  # Delete the queue when the client terminates
                "exclusive": False,  # Allow multiple simultaneous consumers
                "arguments": {},
            }
        }
        binding = {
            "exchange": "amq.topic",  # The AMQP exchange to bind our queue to
            "queue": queue_name,  # The unique name of our queue on the AMQP broker
            # The topics that should be delivered to the queue
            "routing_keys": ["org.fedoraproject.prod.copr.build.end"],
        }

        # Start consuming messages using our callback. This call will block until
        # a KeyboardInterrupt is raised, or the process receives a SIGINT or SIGTERM
        # signal.
        api.consume(
            Consumerino.fedora_messaging_callback, bindings=binding, queues=queues
        )
