from typing import Union

from .client_credentials import ClientCredentialsAuthnConfig
from .none import NoAuthnConfig
from .preshared import PresharedSecretAuthnConfig

AuthnConfig = Union[
    NoAuthnConfig, PresharedSecretAuthnConfig, ClientCredentialsAuthnConfig
]
