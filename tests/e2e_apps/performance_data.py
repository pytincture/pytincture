import time

from pytincture.dataclass import backend_for_frontend


@backend_for_frontend
class PerformanceData:
    def ping(self, value=1):
        return {"ok": True, "value": value}

    def slow(self, seconds=0.1):
        time.sleep(min(max(float(seconds), 0), 0.5))
        return {"ok": True}
