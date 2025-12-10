import asyncio

import psutil

from privateindexer_client.core.config import MEMORY_LOG_INTERVAL
from privateindexer_client.core.logger import log

_max_memory = 0
process = psutil.Process()


def format_bytes(num_bytes: int) -> str:
    """
    Helper to format bytes into a human-readable string
    """
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.2f} KiB"
    if num_bytes < 1024 ** 3:
        return f"{num_bytes / (1024 ** 2):.2f} MiB"
    return f"{num_bytes / (1024 ** 3):.2f} GiB"


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
            for c in children:
                if c.is_running():
                    child_mem = c.memory_full_info().uss
                    mem_children += child_mem
                    log.info(f"[MEMORY] Worker PID {c.pid}: {format_bytes(child_mem)}")

            # total used by main thread and workers
            mem_total = mem_main + mem_children

            # keep track of max utilization
            _max_memory = max(_max_memory, mem_total)

            log.info(f"[MEMORY] Main: {format_bytes(mem_main)} | "
                     f"Workers: {format_bytes(mem_children)} | "
                     f"Total: {format_bytes(mem_total)} | "
                     f"Peak: {format_bytes(_max_memory)}")

        except Exception as e:
            log.error(f"[MEMORY] Error during memory task: {e}")
