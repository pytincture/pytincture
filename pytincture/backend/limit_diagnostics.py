"""Value-free diagnostics for configured resource limits."""

from dataclasses import asdict, dataclass
import math


_LIMITS = {
    "BFF_RESULT_MAX_ITEMS": ("BFF result item limit exceeded", "aggregate items"),
    "BFF_RESULT_MAX_BYTES": ("BFF result byte limit exceeded", "bytes"),
    "BFF_RESULT_MAX_DEPTH": ("BFF result nesting limit exceeded", "levels"),
    "BFF_REQUEST_MAX_ITEMS": ("BFF JSON item limit exceeded", "aggregate items"),
    "BFF_REQUEST_MAX_BYTES": ("BFF request body is too large", "bytes"),
    "BFF_REQUEST_MAX_DEPTH": ("BFF JSON nesting limit exceeded", "levels"),
    "MAX_REQUEST_BODY_BYTES": ("Request body too large", "bytes"),
    "BFF_CALL_TIMEOUT_SECONDS": ("BFF call timed out", "seconds"),
    "BFF_QUEUE_TIMEOUT_SECONDS": ("BFF admission timed out", "seconds"),
    "BFF_MAX_QUEUE": ("BFF admission queue is full", "queued calls"),
    "BFF_REQUEST_INGRESS_TIMEOUT_SECONDS": ("BFF request body timed out", "seconds"),
    "BFF_REQUEST_INGRESS_MAX_CONCURRENCY_PER_PEER": ("BFF upload concurrency exceeded", "concurrent uploads"),
    "BFF_REQUEST_INGRESS_MAX_QUEUE": ("BFF upload queue is full", "queued uploads"),
    "BFF_REQUEST_INGRESS_QUEUE_TIMEOUT_SECONDS": ("BFF upload admission timed out", "seconds"),
    "BFF_ISOLATED_MAX_CONCURRENCY": ("Isolated BFF capacity is exhausted", "child processes"),
    "BFF_ISOLATED_MAX_PER_USER": ("Isolated BFF per-user capacity is exhausted", "child processes"),
    "BFF_STREAM_MAX_BYTES": ("BFF stream byte limit exceeded", "bytes"),
    "BFF_STREAM_MAX_ITEMS": ("BFF stream item limit exceeded", "items"),
    "BFF_STREAM_MAX_SECONDS": ("BFF stream duration exceeded", "seconds"),
    "BFF_STREAM_IDLE_TIMEOUT_SECONDS": ("BFF stream idle timeout exceeded", "seconds"),
    "BFF_STREAM_WRITE_TIMEOUT_SECONDS": ("BFF stream write timeout exceeded", "seconds"),
}

_STAGES = {
    "materialization", "JSON conversion", "JSON encoding", "request parsing",
    "request buffering", "Content-Length", "async collection", "response body",
    "admission", "execution", "streaming",
}


@dataclass(frozen=True)
class LimitViolation:
    setting: str
    limit: int | float
    observed: int | float | None
    stage: str

    def __post_init__(self):
        if (
            self.setting not in _LIMITS or self.stage not in _STAGES
            or type(self.limit) not in (int, float)
            or (type(self.limit) is float and not math.isfinite(self.limit))
            or self.limit < 0
            or (self.observed is not None and (
                type(self.observed) not in (int, float)
                or (type(self.observed) is float and not math.isfinite(self.observed))
                or self.observed < self.limit
            ))
        ):
            raise ValueError("Invalid limit diagnostic")

    def __str__(self):
        label, unit = _LIMITS[self.setting]
        detail = f"{label}: {self.setting}={self.limit}; "
        if self.observed is not None:
            detail += f"observed at least {self.observed} {unit} during {self.stage}"
        else:
            detail += f"limit reached during {self.stage}"
        if self.setting.endswith("_MAX_ITEMS"):
            detail += ". Items count list entries and object fields across nested containers, not bytes"
        return detail


class LimitDetail(str):
    """A readable HTTP detail carrying only framework-owned limit metadata."""

    def __new__(cls, violation: LimitViolation):
        instance = super().__new__(cls, str(violation))
        instance.violation = violation
        return instance

    def headers(self) -> dict[str, str]:
        headers = {
            "X-Pytincture-Limit": self.violation.setting,
            "X-Pytincture-Limit-Value": str(self.violation.limit),
        }
        if self.violation.observed is not None:
            headers["X-Pytincture-Limit-Observed"] = str(self.violation.observed)
        return headers

    def payload(self) -> dict:
        return {"detail": str(self), "limit": {**asdict(self.violation), "observed_is_lower_bound": True}}


def limit_detail(setting: str, limit: int | float, observed: int | float | None, stage: str) -> LimitDetail:
    return LimitDetail(LimitViolation(setting, limit, observed, stage))
