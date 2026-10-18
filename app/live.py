"""Live progress of one agent run, between threads of this process.

The agent loop runs on the agent server; its tools call back into this app (agent_tools.py), while the chat stream of
the same run is proxied here (luna._via_agent). A search takes seconds; its stages (embedding, comparison, reranking)
are published to the run's channel and merged into the chat stream as they happen. One queue per run id, dropped when
the run ends. The service runs as a single instance, so the tool call and the stream meet in this process.
"""
import queue
import threading

_channels, _lock = {}, threading.Lock()


def open_run(run):
  with _lock:
    return _channels.setdefault(str(run), queue.Queue())


def publish(run, event):
  with _lock:
    q = _channels.get(str(run))
  if q is not None:
    q.put(event)


def close(run):
  with _lock:
    _channels.pop(str(run), None)
