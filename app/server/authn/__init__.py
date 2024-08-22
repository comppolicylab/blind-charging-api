from typing import Union

from .none import NoAuthnConfig
from .preshared import PresharedSecretAuthnConfig

AuthnConfig = Union[NoAuthnConfig, PresharedSecretAuthnConfig]
