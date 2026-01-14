"""
Custom exceptions for the Aula Login Client module.
"""


class AulaAuthenticationError(Exception):
    """Base exception for Aula authentication errors."""
    pass


class MitIDError(AulaAuthenticationError):
    """Exception raised for MitID authentication failures."""
    pass


class TokenExpiredError(AulaAuthenticationError):
    """Exception raised when access token has expired."""
    pass


class APIError(AulaAuthenticationError):
    """Exception raised for API access errors."""
    pass


class ConfigurationError(AulaAuthenticationError):
    """Exception raised for configuration or setup errors."""
    pass


class NetworkError(AulaAuthenticationError):
    """Exception raised for network-related errors."""
    pass


class SAMLError(AulaAuthenticationError):
    """Exception raised for SAML flow errors."""
    pass


class OAuthError(AulaAuthenticationError):
    """Exception raised for OAuth flow errors."""
    pass