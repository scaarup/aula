from .client import AulaLoginClient
from .exceptions import (
    AulaAuthenticationError,
    MitIDError,
    TokenExpiredError,
    APIError
)
__version__ = "1.0.0"
__author__ = "mchrdk"
__description__ = "Aula platform authentication client with MitID integration"

__all__ = [
    "AulaLoginClient",
    "AulaAuthenticationError",
    "MitIDError",
    "TokenExpiredError",
    "APIError"
]