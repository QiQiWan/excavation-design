"""Lease-based Agent worker; can run embedded with FastAPI or as a separate process.

Usage: python -m app.agents.worker --once / --loop
"""
from __future__ import annotations
import argparse,logging,time
from threading import Event,Thread
from .engine import AgentRuntime

logger=logging.getLogger('pitguard.agent')


class AgentWorker:
    def __init__(self,runtime=None,poll_seconds=1.0):
        self.runtime=runtime or AgentRuntime()
        self.poll_seconds=poll_seconds
        self.stop_event=Event()
        self.thread=None

    def poll_once(self):
        return self.runtime.process_one()

    def run(self):
        while not self.stop_event.is_set():
            try:
                handled=self.poll_once()
            except Exception:
                logger.exception('Agent worker poll failed')
                handled=None
            if handled is None:self.stop_event.wait(self.poll_seconds)

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=Thread(target=self.run,name='pitguard-agent-worker',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=4)


def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--once',action='store_true')
    g.add_argument('--loop',action='store_true')
    args=p.parse_args()
    worker=AgentWorker()
    if args.once:
        item=worker.poll_once()
        print('no queued Agent task' if item is None else f"{item['id']} {item['status']} {item['step']}")
    else:
        print('PitGuard V3.88 Agent worker running; Ctrl+C to stop',flush=True)
        try:worker.run()
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
