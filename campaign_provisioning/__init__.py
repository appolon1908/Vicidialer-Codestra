"""Safe campaign design and provisioning primitives."""

from .design import (
    CampaignDesignRequest,
    CampaignDesignService,
    DesignConflict,
    DesignValidationError,
    SQLiteDesignRepository,
)
from .lifecycle import (
    LifecycleError,
    LifecycleStore,
    PrivateAdapterClient,
    PrivateAdapterConfig,
    run_synthetic_lifecycle,
)

__all__ = [
    "CampaignDesignRequest",
    "CampaignDesignService",
    "DesignConflict",
    "DesignValidationError",
    "SQLiteDesignRepository",
    "LifecycleError",
    "LifecycleStore",
    "PrivateAdapterClient",
    "PrivateAdapterConfig",
    "run_synthetic_lifecycle",
]
