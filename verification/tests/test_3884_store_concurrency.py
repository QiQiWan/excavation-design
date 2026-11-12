"""AgentStore 并发写回归测试。

Windows 上 SQLite 由最后一个连接关闭时删除 -wal/-shm；若另一线程恰好打开
同一个库，新连接会拿到失效的 WAL 上下文，BEGIN IMMEDIATE 报
"attempt to write a readonly database"。这里用高频短连接复现该场景。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

from app.agents.store import AgentStore


def test_parallel_short_connections_never_go_readonly(tmp_path):
    db = tmp_path / 'concurrent.sqlite3'
    store = AgentStore(db)
    conversation = store.create_conversation(project_id='P-CONCURRENCY', actor='tester')

    def write(index: int) -> int:
        # 每个任务都新建 AgentStore，最大化开/关连接频率。
        local = AgentStore(db)
        local.append_message(conversation['id'], role='user', content=f'message-{index}')
        local.get_conversation(conversation['id'])
        local.messages(conversation['id'])
        return index

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(120)))

    assert len(results) == 120
    assert len(store.messages(conversation['id'], limit=200)) == 120
    with sqlite3.connect(db) as raw:
        assert raw.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
