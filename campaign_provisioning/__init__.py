"""Safe campaign design and provisioning primitives."""

from .design import (
    CampaignDesignRequest,
    CampaignDesignService,
    DesignConflict,
    DesignValidationError,
    SQLiteDesignRepository,
)
from .lifecycle import (
    AdapterRequestError,
    LifecycleError,
    LifecycleStore,
    PrivateAdapterClient,
    PrivateAdapterConfig,
    run_synthetic_lifecycle,
)

__all__ = [
    "AdapterRequestError",
    "CampaignDesignRequest",
    "CampaignDesignService",
    "DesignConflict",
    "DesignValidationError",
    "LifecycleError",
    "LifecycleStore",
    "PrivateAdapterClient",
    "PrivateAdapterConfig",
    "SQLiteDesignRepository",
    "run_synthetic_lifecycle",
]
