import asyncio

import psutil

from privateindexer_client.core.config import MEMORY_LOG_INTERVAL
from privateindexer_client.core.logger import log
from privateindexer_client.core.utils import format_bytes

_max_memory = 0
process = psutil.Process()


async def periodic_memory_task():
    """
    Periodically logs memory utilization of process to console
    """
    global _max_memory
    log.debug("[MEMORY] Task loop started")
    while True:
        await asyncio.sleep(MEMORY_LOG_INTERVAL)
        try:
            # main thread memory utilization
            mem_main = process.memory_full_info().uss

            # collect the process workers memory utilization
            children = process.children(recursive=True)
            mem_children = 0
            active_child_count = 0
            for c in children:
                if c.is_running():
                    child_mem = c.memory_full_info().uss
                    if child_mem == 0:
                        continue
                    mem_children += child_mem
                    active_child_count += 1

            # total used by main thread and workers
            mem_total = mem_main + mem_children

            # keep track of max utilization
            _max_memory = max(_max_memory, mem_total)

            log.info(f"[MEMORY] Main: {format_bytes(mem_main)} | "
                     f"Children: {format_bytes(mem_children)} ({active_child_count} active processes) | "
                     f"Total: {format_bytes(mem_total)} | "
                     f"Peak: {format_bytes(_max_memory)}")

        except Exception as e:
            log.error(f"[MEMORY] Exception during memory task: {e}")
