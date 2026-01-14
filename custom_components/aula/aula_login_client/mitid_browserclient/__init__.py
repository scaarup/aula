"""
MitID BrowserClient - Unofficial Python implementation of the MitID JavaScript browser client.

This package provides functionality to authenticate with MitID using various methods
including the MitID app (OTP and QR Code) and code token reader devices.
"""

from .BrowserClient import BrowserClient
from .CustomSRP import CustomSRP
from .Helpers import (
    get_default_args,
    process_args,
    get_authentication_code,
    choose_between_multiple_identitites,
    generate_nem_login_parameters,
)

__version__ = "1.0.0"
__author__ = "Hundter"
__license__ = "MIT"

__all__ = [
    "BrowserClient",
    "CustomSRP", 
    "get_default_args",
    "process_args",
    "get_authentication_code",
    "choose_between_multiple_identitites",
    "generate_nem_login_parameters",
]