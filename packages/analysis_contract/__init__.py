from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import ContractService, ContractViolation
    from .state import StaleBaseRevision

__all__ = ["ContractService", "ContractViolation", "StaleBaseRevision"]

def __getattr__(name: str):
    pass
    if name in {"ContractService", "ContractViolation"}:
        from .service import ContractService, ContractViolation

        value = {
            "ContractService": ContractService,
            "ContractViolation": ContractViolation,
        }[name]
    elif name == "StaleBaseRevision":
        from .state import StaleBaseRevision

        value = StaleBaseRevision
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value

def __dir__():
    return sorted(set(globals()) | set(__all__))
