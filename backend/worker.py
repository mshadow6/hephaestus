"""Entrypoint du worker RQ : dépile les jobs de provisioning VM.

Usage local : python worker.py
"""

import logging

from rq import Worker

from app.queue import redis_conn, vm_queue

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    worker = Worker([vm_queue], connection=redis_conn)
    worker.work()
