from redis import Redis
from rq import Queue

from app.config import settings

redis_conn = Redis.from_url(settings.redis_url)
vm_queue = Queue(settings.rq_queue_name, connection=redis_conn)
