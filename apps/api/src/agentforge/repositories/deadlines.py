"""One deadline source; legacy/standalone tasks retain their original policy."""
from datetime import timedelta


def task_deadline(task):
    return getattr(task, "execution_deadline", None) or task.created_at + timedelta(seconds=task.max_wall_time_sec)
